#!/usr/bin/env python3
"""Prepare openstreetmap-carto's project.mml for host-side ground-truth rendering.

Two edits:

1. Point the osm2pgsql datasource at the database port the compose override
   publishes, so a mapnik running on the host can read it.

Adding `osm_id` to the queries is a separate step: see patch_project_queries.py.
"""

import sys

HOST_BLOCK = '''  osm2pgsql: &osm2pgsql
    type: "postgis"
    dbname: "gis"
    host: "127.0.0.1"
    port: "54321"
    user: "postgres"
'''


def add_connection(text):
    old = '  osm2pgsql: &osm2pgsql\n    type: "postgis"\n    dbname: "gis"\n'
    if 'port: "54321"' in text:
        return text, False
    if old not in text:
        sys.exit("could not find the osm2pgsql datasource block in project.mml")
    return text.replace(old, HOST_BLOCK), True


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "project.mml"
    text = open(path).read()
    text, connected = add_connection(text)
    open(path, "w").write(text)
    print("{}: datasource {} host-reachable".format(path, "made" if connected else "already"))


if __name__ == "__main__":
    main()
