import logging
import os
import subprocess
from pathlib import Path

REPO_URL = "https://www.modelscope.cn/ResembleAI/resemble-enhance.git"
REPO_DIR = Path(__file__).parent.parent / "model_repo"

logger = logging.getLogger(__name__)


def _is_lfs_pointer(path: Path) -> bool:
    """LFS 指针文件以 'version https://git-lfs' 开头；真实模型以 PK\\x03\\x04 开头。"""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        return head.startswith(b"version https://git-lfs")
    except OSError:
        return True


def _lfs_files_already_pulled() -> bool:
    """真实 .pt 已是真文件（非 134 字节 LFS 指针）→ 跳过 pull。"""
    real_pt = REPO_DIR / "enhancer_stage2" / "ds" / "G" / "default" / "mp_rank_00_model_states.pt"
    if not real_pt.exists() or real_pt.stat().st_size < 1_000_000:
        return False
    return not _is_lfs_pointer(real_pt)


def run_command(command, msg=None, env={}):
    try:
        subprocess.run(command, check=True, env={**os.environ, **env})
    except subprocess.CalledProcessError as e:
        if msg is not None:
            raise RuntimeError(msg) from e
        raise e


def download():
    logger.info("Downloading the model...")

    if REPO_DIR.exists() and (REPO_DIR / ".git").exists():
        if _lfs_files_already_pulled():
            # LFS 真文件已在 → 跳过 git pull/lfs pull，避免无网环境 75s 超时
            logger.info("LFS files already present, skipping git pull.")
        else:
            logger.info("Repository exists but LFS files missing, attempting to pull...")
            run_command(
                ["git", "-C", str(REPO_DIR), "pull"],
                "Failed to pull latest changes, please try again.",
                {"GIT_LFS_SKIP_SMUDGE": "1"},
            )
            logger.info("Pulling large files...")
            run_command(
                ["git", "-C", str(REPO_DIR), "lfs", "pull"],
                "Failed to pull latest changes, please try again.",
            )
    else:
        logger.info("Cloning the repository...")
        run_command(
            ["git", "clone", REPO_URL, str(REPO_DIR)],
            "Failed to clone the repository, please try again.",
            {"GIT_LFS_SKIP_SMUDGE": "1"},
        )
        logger.info("Pulling large files...")
        run_command(
            ["git", "-C", str(REPO_DIR), "lfs", "pull"],
            "Failed to pull latest changes, please try again.",
        )

    run_dir = REPO_DIR / "enhancer_stage2"

    return run_dir
