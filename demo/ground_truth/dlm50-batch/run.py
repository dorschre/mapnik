"""Resumable, rate-limited acquisition with bounded render/validation workers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import fcntl
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[3]
PIPELINE=ROOT.parent/'otto-usecase-3/data-pipeline/gpkg-to-dtk50'
ADAPTER=ROOT.parent/'otto-usecase-3/data-pipeline/adv-dlm50-adapter'
sys.path.insert(0,str(ADAPTER/'src'))
from adv_dlm50_adapter.acquisition import acquire_dlm
from adv_dlm50_adapter.fetch_adv import Client
from adv_dlm50_adapter.road_parents import enrich
from gallery import initialize
import requests


def atomic_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2));temporary.replace(path)


class RetryClient(Client):
    def get(self,url):
        return self._retry(super().get,url)

    def post(self,url,form):
        return self._retry(super().post,url,form)

    def _retry(self,method,*args):
        for attempt in range(3):
            try: return method(*args)
            except (requests.Timeout,requests.ConnectionError) as error:
                if attempt==2: raise
                print('HTTP retry:',type(error).__name__,flush=True)
            except ValueError as error:
                if attempt==2 or not any(f'HTTP {code}' in str(error) for code in (429,500,502,503,504)):
                    raise
                print('HTTP retry:',str(error)[:120],flush=True)
            time.sleep(15*(attempt+1))


class CompressedRetryClient(RetryClient):
    """Losslessly archive response bodies while preserving the adapter cache hashes."""
    def _compressed(self, key, method, *args):
        target = self.cache / (key + '.body')
        packed = self.cache / (key + '.body.gz')
        if not target.exists() and packed.exists():
            target.write_bytes(gzip.decompress(packed.read_bytes()))
        if shutil.disk_usage(self.cache.parent).free < 16_000_000_000:
            raise RuntimeError('Less than 16 GB free; stopped before another HTTP request')
        result = method(*args)
        if target.exists():
            temporary = packed.with_suffix('.tmp')
            data = target.read_bytes()
            encoded = gzip.compress(data, compresslevel=6, mtime=0)
            assert gzip.decompress(encoded) == data
            temporary.write_bytes(encoded)
            temporary.replace(packed)
            target.unlink()
        return result

    def get(self, url):
        return self._compressed(hashlib.sha256(url.encode()).hexdigest(), super().get, url)

    def post(self, url, form):
        body = urlencode(form).encode()
        key = hashlib.sha256(b'POST\n' + url.encode() + b'\n' + body).hexdigest()
        return self._compressed(key, super().post, url, form)


def compact_snapshot(snapshot):
    """Verify every archived byte before removing this batch's raw snapshot."""
    if snapshot.is_symlink():
        return
    archive = snapshot.with_suffix('.tar.gz')
    temporary = archive.with_suffix('.tmp')
    files = sorted(p for p in snapshot.rglob('*') if p.is_file())
    expected = {str(p.relative_to(snapshot)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with tarfile.open(temporary, 'w:gz', compresslevel=6) as packed:
        for path in files:
            packed.add(path, arcname=str(path.relative_to(snapshot)))
    with tarfile.open(temporary, 'r:gz') as packed:
        actual = {m.name: hashlib.sha256(packed.extractfile(m).read()).hexdigest() for m in packed.getmembers()}
    if actual != expected:
        raise ValueError('Snapshot archive verification failed; raw files retained')
    temporary.replace(archive)
    atomic_json(archive.with_suffix('.json'), dict(files=expected, sha256=hashlib.sha256(archive.read_bytes()).hexdigest()))
    shutil.rmtree(snapshot)


def restore_snapshot(snapshot):
    archive = snapshot.with_suffix('.tar.gz')
    if snapshot.exists() or not archive.exists():
        return
    expected = json.loads(archive.with_suffix('.json').read_text())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected['sha256']:
        raise ValueError('Snapshot archive checksum mismatch')
    with tarfile.open(archive, 'r:gz') as packed:
        for member in packed.getmembers():
            path = snapshot / member.name
            if not member.isfile() or not path.resolve().is_relative_to(snapshot.resolve()):
                raise ValueError('Invalid archive member')
            data = packed.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != expected['files'][member.name]:
                raise ValueError('Archived snapshot file checksum mismatch')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch',type=Path);parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--compress-cache', action='store_true', help='Losslessly gzip cached HTTP response bodies')
    parser.add_argument('--compact-snapshots', action='store_true', help='Verify and gzip source snapshots after successful rendering')
    args=parser.parse_args();batch=args.batch.resolve()
    guard=(batch/'.batch.lock').open('a')
    fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    logging.getLogger('rdflib.term').setLevel(logging.CRITICAL)
    areas=json.loads((batch/'areas.json').read_text());initialize(batch)
    (batch/'logs').mkdir(exist_ok=True);(batch/'snapshots').mkdir(exist_ok=True)
    client=(CompressedRetryClient if args.compress_cache else RetryClient)(batch/'request-cache')
    rows={a['name']:dict(name=a['name'],label=a['label'],state=a['states'][0],status='queued') for a in areas}
    lock=threading.Lock();work=queue.Queue(maxsize=args.workers*2);acquisition_done=threading.Event()
    previous=ROOT/'build/dtk50-acquisition-fixed'
    old_areas={a['name']:a for a in json.loads((previous/'areas.json').read_text())} if (previous/'areas.json').exists() else {}
    public=batch/'public'
    env=dict(os.environ,MAPNIK_FONT_DIRS=str(PIPELINE/'src/map_symbology/resources/atkis/text/fonts/univers')+':'+str(ROOT/'fonts/dejavu-fonts-ttf-2.37/ttf'))

    def update(name,**kwargs):
        with lock: rows[name].update(kwargs)

    def publish(area,result):
        source=batch/area['name']/'dtk50';target=public/area['name'];target.mkdir(exist_ok=True)
        for filename in ('map.png','map.json','map.ttl','coverage-report.json','validation.json','manifest.json','shacl-report.txt'):
            destination=target/filename
            if destination.exists(): destination.unlink()
            os.link(source/filename,destination)
        update(area['name'],status='ready',validation=result)

    def produce():
        try:
            for area in areas:
                name=area['name'];state=area['states'][0];snapshot=batch/'snapshots'/name/state
                validation=batch/name/'dtk50/validation.json'
                if validation.exists():
                    result=json.loads(validation.read_text())
                    if result.get('nonblank_image') and result.get('shacl_conforms'):
                        publish(area,result);continue
                try:
                    if shutil.disk_usage(batch).free<16_000_000_000: raise RuntimeError('Less than 16 GB free; paused acquisition')
                    if not snapshot.exists() and name in old_areas and area['bbox']==old_areas[name]['bbox']:
                        old=previous/'snapshots'/name/state
                        snapshot.parent.mkdir(parents=True,exist_ok=True)
                        snapshot.symlink_to(old,target_is_directory=True)
                    restore_snapshot(snapshot)
                    update(name,status='acquiring')
                    manifest=snapshot/'manifest.json'
                    if manifest.exists():
                        metadata=json.loads(manifest.read_text())
                        if metadata['bbox']!=area['bbox']:
                            raise ValueError('Saved snapshot covers a different map extent')
                        if hashlib.sha256((snapshot/metadata['rdf_file']).read_bytes()).hexdigest()!=metadata['sha256']:
                            raise ValueError('Snapshot checksum mismatch')
                        if not metadata.get('road_dependency_acquisition',{}).get('complete'):
                            enrich(snapshot,snapshot,client.cache,client=client,batch_size=512)
                    else:
                        acquire_dlm(area,batch/'snapshots',client,page_size=2000,parent_batch_size=512)
                    update(name,status='waiting to render')
                    work.put(area)
                except Exception as error:
                    update(name,status='failed',error=str(error),failed_stage='acquisition')
                    print(name,'ACQUISITION FAILED',repr(error),flush=True)
        finally: acquisition_done.set()

    def render_validate(area):
        name=area['name'];log=batch/'logs'/(name+'.log')
        try:
            update(name,status='rendering')
            with log.open('a') as output:
                subprocess.run([str(PIPELINE/'.venv/bin/python'),str(ROOT/'demo/ground_truth/berlin-batch/render_dtk50.py'),
                    str(batch),'--only',name,'--allow-incomplete-preview'],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,check=True)
                update(name,status='validating graph')
                subprocess.run([str(ROOT/'.venv/bin/python'),str(Path(__file__).with_name('worker.py')),str(batch),name],
                    cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,check=True)
            result=json.loads((batch/name/'dtk50/validation.json').read_text())
            if args.compact_snapshots:
                compact_snapshot(batch/'snapshots'/name/area['states'][0])
            publish(area,result)
            print(name,'READY',result['visible_objects'],'objects',flush=True)
        except Exception as error:
            update(name,status='failed',error=str(error),failed_stage='render/validation')
            print(name,'RENDER/VALIDATION FAILED',repr(error),flush=True)

    def progress(phase,archives=()):
        with lock: current=[dict(rows[a['name']]) for a in areas]
        payload=dict(updated_at=datetime.now(timezone.utc).isoformat(),phase=phase,
                     ready=sum(r['status']=='ready' for r in current),total=len(areas),areas=current,archives=list(archives))
        atomic_json(public/'progress.json',payload);atomic_json(batch/'progress.json',payload)
        return payload

    thread=threading.Thread(target=produce,daemon=True);thread.start();futures=[]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        while not acquisition_done.is_set() or not work.empty() or any(not f.done() for f in futures):
            futures=[f for f in futures if not f.done()]
            if len(futures)<args.workers:
                try: futures.append(executor.submit(render_validate,work.get(timeout=1)))
                except queue.Empty: pass
            else: time.sleep(1)
            progress('Acquiring, rendering and validating')
    thread.join();payload=progress('Preparing downloads')
    archives=[]
    for state in dict.fromkeys(a['states'][0] for a in areas):
        selected=[a for a in areas if a['states'][0]==state]
        if not all(rows[a['name']]['status']=='ready' for a in selected): continue
        filename=state.lower()+'.zip';temporary=public/(filename+'.tmp')
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=4) as archive:
            archive.write(public/'README.md','README.md')
            archive.writestr('areas.json',json.dumps(selected,ensure_ascii=False,indent=2))
            for area in selected:
                for path in sorted((public/area['name']).iterdir()): archive.write(path,str(path.relative_to(public)))
        temporary.replace(public/filename);archives.append(dict(state=state,file=filename,count=len(selected)))
        progress('Preparing downloads',archives)
    ready=[rows[a['name']] for a in areas if rows[a['name']]['status']=='ready']
    (public/'dataset.jsonl').write_text(''.join(json.dumps(dict(row,map=row['name']+'/map.png',knowledge_graph=row['name']+'/map.ttl'),ensure_ascii=False)+'\n' for row in ready))
    files=[p for p in public.rglob('*') if p.is_file() and p.name not in ('SHA256SUMS','progress.json')]
    (public/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(public))+'\n' for p in sorted(files)))
    result=progress('Complete' if len(ready)==len(areas) else 'Incomplete — see failed areas',archives)
    print('FINISHED',result['ready'],'/',len(areas),flush=True)
    if result['ready']!=len(areas): raise SystemExit(1)

if __name__=='__main__': main()
