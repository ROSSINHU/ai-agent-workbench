# Contributing

Thanks for your interest in making the workbench better!

## Quick start
1. Fork this repository and clone your fork.
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes.
4. Test locally:
   ```bash
   python -m py_compile service_manager.py   # syntax check
   WORKBENCH_SMOKE=1 python service_manager.py   # headless smoke test
   ```
5. Commit with a clear message (see Commit messages below).
6. Push and open a Pull Request against the `main` branch.

## Commit messages
- Use English or Chinese — be consistent within a PR.
- Format: `<scope>: <imperative summary>` — e.g. `version: fix EBADENGINE hint for codex`.
- Common scopes: `version`, `upgrade`, `ui`, `launcher`, `proxy`, `docs`, `ci`, `compat`, `deps`, `repo`.

## Code style
- **Python**: PEP 8, 4-space indent, type hints where natural.
- **`.bat` files**: pure ASCII, **CRLF** line endings (enforced by `.gitattributes`).
- **No hardcoded user paths** in source code — runtime paths come from env vars, config files, or auto-detection.
- **New dependencies**: add them to `requirements.txt` and mark them optional if they have a graceful-degradation path (see `psutil` / `pystray` as examples).

## Issues
Use the GitHub issue tracker for bug reports and feature requests. For bugs please include:
- Python version, Windows version, Node version
- Workbench version (tag or commit hash)
- Log snippet (from `logs/workbench-YYYYMMDD.log`)
- Steps to reproduce

## License
By contributing, you agree that your contributions will be licensed under the MIT License.
