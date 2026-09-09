# Repository Guidelines

## Project Structure & Module Organization

Mapnik is a C++ mapping and rendering library. Public headers live in `include/mapnik/`, implementations in `src/`, and datasource plugins in `plugins/input/`. Command-line utilities are in `utils/`; benchmarks are in `benchmark/`. Tests occupy `test/unit/`, `test/standalone/`, and `test/visual/`, with fixtures in `test/data/` and `test/data-visual/`. Fonts live in `fonts/`; third-party dependencies live in `deps/`.

Ground-truth extraction demos, Python bindings, and RDF tooling are in `demo/ground_truth/`; related ontology resources are in `map-display-ontology/`. Read the relevant demo README before changing these workflows.

## Build, Test, and Development Commands

Install dependencies described in `INSTALL.md` and `docs/cmake-usage.md`. The current CMake configuration requires CMake 3.30 and C++20.

- `git submodule update --init --recursive`: initialize dependencies and test fixtures.
- `cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON`: configure a development build.
- `cmake --build build --parallel 2`: compile the library, enabled utilities, and tests; adjust parallelism to available memory.
- `ctest --test-dir build --output-on-failure`: run registered unit and standalone tests.
- `(cd build/out && ./mapnik-test-visual --output-dir visual-test-result)`: run visual comparisons separately.
- `./build/out/mapnik-ground-truth-demo /tmp/mapnik-example`: generate a synthetic map and ground-truth JSON when that demo is built.

The SCons alternative is `./configure`, `make JOBS=2`, then `make test`.

## Coding Style & Naming Conventions

Follow `.clang-format`: four spaces, 120-column lines, and separate-line braces for functions and control statements. Use C++20, `type const&`, C++ casts, and smart pointers. Match neighboring lowercase, underscore-separated names and `.hpp`/`.cpp` filenames. Run `pre-commit run --files <changed-files>`; hooks use clang-format 18.1.3 and check CMake whitespace.

## Testing Guidelines

Add regression tests for fixes and tests for new features, following `docs/contributing.md`. CMake uses Catch2 v2.13.7. Place tests beside related cases, using descriptive `TEST_CASE` names and existing filename patterns such as `*_test.cpp`. Register new unit sources in `test/CMakeLists.txt`. For rendering changes, inspect visual differences before updating reference images. CI collects coverage; no numeric threshold is configured locally.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, such as “Emit the geographic viewport in the ground truth JSON.” Keep commits focused; reference related issues with `refs #123` or `closes #123`. Update `CHANGELOG.md` for notable features and fixes. PRs should explain the behavior change, link relevant issues, and report validation commands and results. Include comparison images for rendering changes. Follow `CODE_OF_CONDUCT.md`.
