# Tech Debt Harness tests

Unit tests for `scripts/debt_scan.py`. Standard-library only — run with:

```bash
python -m unittest discover -s tests -v
```

```powershell
py -3 -m unittest discover -s tests -v
```

These tests live in the harness **source** repo only; the installers do
not copy `tests/` into target repos. Adding a parser or changing severity
mapping? Add a fixture + assertion here first.
