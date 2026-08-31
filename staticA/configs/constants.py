# staticA/configs/constants.py

from pathlib import Path 

# pipelines_rag            root
project_root: Path=Path(__file__).resolve().parents[2]
assert project_root.is_dir(), f"project_root: {project_root} is not exists!!!"

# pipelines_rag/.env       path 
env_path: Path=project_root / ".env"
assert env_path.is_file(), f"env_path: {env_path} is not exists!!!"

# pipelines_rag/staticA/   root
staticA_root: Path = project_root / "staticA"
assert staticA_root.is_dir(), f"staticA_root: {staticA_root} is not exists!!!"
