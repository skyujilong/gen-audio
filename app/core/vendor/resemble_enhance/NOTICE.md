# Third-party Notices

This project vendors a modified copy of the following open-source library:

---

## resemble-enhance 0.0.1

- **Upstream**: https://github.com/resemble-ai/resemble-enhance
- **License**: MIT (see `LICENSE` in this directory)
- **Copyright**: (c) 2023 Resemble AI
- **Files vendored**: all `.py` under `app/core/vendor/resemble_enhance/` (49 files, ~4575 LOC)
- **Files NOT vendored**: `model_repo/` (1.5 GB of LFS-tracked model weights; loaded at runtime from `$RESEMBLE_ENHANCE_MODEL_REPO` or the pip-installed `resemble_enhance` package location, see `__init__.py`)

### Modifications applied (search for `[VENDOR-FIX]` markers in source)

| File | Original line | Fix | Reason |
|------|---------------|-----|--------|
| `enhancer/lcfm/cfm.py` | 74 | `float(fsolve(...)[0])` | scipy≥1.10 returns 1-D ndarray from `fsolve`; `float()` of 1-D array raises `TypeError` |
| `inference.py` | 123–128 (before `resample(...)`) | prepend `if not torch.is_tensor(dwav): dwav = torch.as_tensor(dwav, dtype=torch.float32)` | `torchaudio.functional.resample` passes `waveform.dtype` into `_get_sinc_resample_kernel(..., dtype=...)`; when `dwav` is numpy, dtype is `numpy.dtype`, which torch≥2.10 rejects in `torch.arange(..., dtype=numpy_dtype)` |
| `__init__.py` | (added, not upstream) | Re-route `download.REPO_DIR` to the pip-installed package's already-downloaded weights (or `$RESEMBLE_ENHANCE_MODEL_REPO` if set) | Avoid 1.5 GB of duplicated weights in the git repo |

The remainder of the vendored code is byte-identical to upstream v0.0.1.

### Keeping the vendor up to date

To refresh from upstream:

```bash
# In a scratch directory:
pip install --no-deps "resemble-enhance==0.0.1"
SRC=$(python -c "import resemble_enhance, os; print(os.path.dirname(resemble_enhance.__file__))")
rsync -a --exclude='__pycache__' --exclude='model_repo' "$SRC/" \
    app/core/vendor/resemble_enhance/
# Then re-apply the [VENDOR-FIX] edits above.
```

Model weights remain separately managed; the vendor startup hooks automatically reuse
the pip-installed package's `model_repo` if it exists.
