import subprocess
from pathlib import Path
import shutil
import sys

def create_uv_venv(requirements_path: str, venv_name: str) -> str:
    req_path = Path(requirements_path)
    if not req_path.exists():
        raise FileNotFoundError(f"Requirements file not found: {requirements_path}")
    
    env_dir = Path("layerlite_env")
    env_dir.mkdir(exist_ok=True)
    
    venv_path = env_dir / venv_name
    if venv_path.exists():
        shutil.rmtree(venv_path)

    try:
        subprocess.run(
            ["uv", "venv", str(venv_path)],
            check=True,
            capture_output=True,
            text=True
        )
        
        subprocess.run(
            ["uv", "pip", "install", "-r", str(req_path), "--python", str(venv_path / "bin" / "python")],
            check=True,
            capture_output=True,
            text=True
        )
        return str(venv_path.absolute())
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create venv: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("uv command not found. Please install uv first: pip install uv")

def get_directory_size(path: Path) -> int:
    total_size = 0
    try:
        for item in path.rglob('*'):
            if item.is_dir() and item.name == '__pycache__':
                continue
            if item.is_file() and item.suffix == '.pyc':
                continue
            if item.is_file():
                total_size += item.stat().st_size
    except (OSError, PermissionError):
        pass
    return total_size


def count_python_files(path: Path) -> int:
    count = 0
    try:
        for item in path.rglob('*.py'):
            if item.is_file():
                count += 1
    except (OSError, PermissionError):
        pass
    return count


def count_all_files(path: Path) -> int:
    count = 0
    try:
        for item in path.rglob('*'):
            if item.is_dir() and item.name == '__pycache__':
                continue
            if item.is_file() and item.suffix == '.pyc':
                continue
            if item.is_file():
                count += 1
    except (OSError, PermissionError):
        pass
    return count


def get_python_files_size(path: Path) -> int:
    total_size = 0
    try:
        for item in path.rglob('*.py'):
            if item.is_file():
                total_size += item.stat().st_size
    except (OSError, PermissionError):
        pass
    return total_size


def get_top_non_python_file_types(path: Path, top_n: int = 3) -> list:
    file_type_counts = {}
    try:
        for item in path.rglob('*'):
            if item.is_dir() and item.name == '__pycache__':
                continue
            if item.is_file() and item.suffix == '.pyc':
                continue
            if item.is_file() and item.suffix != '.py':
                ext = item.suffix.lower() if item.suffix else '(no ext)'
                file_type_counts[ext] = file_type_counts.get(ext, 0) + 1
    except (OSError, PermissionError):
        pass

    sorted_types = sorted(file_type_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_types[:top_n]


def measure_venv_size(venv_path: str, detailed: bool = True) -> dict:

    venv_dir = Path(venv_path)
    if not venv_dir.exists():
        raise FileNotFoundError(f"Virtual environment not found: {venv_path}")
    
    total_bytes = get_directory_size(venv_dir)
    total_mb = total_bytes / (1024 * 1024)

    result = {
        'total_size_mb': round(total_mb, 2),
        'total_size_bytes': total_bytes
    }
    
    if detailed:
        site_packages = None
        
        possible_paths = [
            venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
            venv_dir / "Lib" / "site-packages",  # Windows
        ]
        
        for path in possible_paths:
            if path.exists():
                site_packages = path
                break
        
        if site_packages:
            packages = {}
            
            for item in site_packages.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    if item.name not in ['__pycache__', 'pip', 'setuptools', 'wheel']:
                        package_bytes = get_directory_size(item)
                        package_mb = package_bytes / (1024 * 1024)
                        python_size_bytes = get_python_files_size(item)
                        python_size_mb = python_size_bytes / (1024 * 1024)
                        top_file_types = get_top_non_python_file_types(item)
                        if package_mb > 0.01:
                            packages[item.name] = {
                                'size_mb': round(package_mb, 2),
                                'size_bytes': package_bytes,
                                'python_files': count_python_files(item),
                                'total_files': count_all_files(item),
                                'python_size_mb': round(python_size_mb, 2),
                                'python_size_bytes': python_size_bytes,
                                'top_non_python_types': top_file_types
                            }
            sorted_packages = dict(sorted(packages.items(), 
                                        key=lambda x: x[1]['size_mb'], 
                                        reverse=True))
            
            result['packages'] = sorted_packages

            cache_files = 0
            cache_bytes = 0
            try:
                for f in site_packages.rglob('*.pyc'):
                    if f.is_file():
                        cache_files += 1
                        cache_bytes += f.stat().st_size
            except (OSError, PermissionError):
                pass
            result['cache_stats'] = {
                'files': cache_files,
                'bytes': cache_bytes,
                'mb': round(cache_bytes / (1024 * 1024), 2)
            }

            total_bytes_pkgs = sum(info['size_bytes'] for info in sorted_packages.values())
            result['total_size_bytes'] = total_bytes_pkgs
            result['total_size_mb'] = round(total_bytes_pkgs / (1024 * 1024), 2)

            result['site_packages_path'] = str(site_packages)
        else:
            result['packages'] = {}
            result['cache_stats'] = {'files': 0, 'bytes': 0, 'mb': 0.0}
            result['site_packages_path'] = None
    
    print(f"\nPackage breakdown ({len(result['packages'])} packages):")
    print("-" * 140)
    print(f"{'Package':<30} {'Total Size':<12} {'Python Size':<13} {'Total Files':<12} {'Python Files':<13} {'Top Non-Python Types':<40}")
    print("-" * 140)
    for package, info in result['packages'].items():
        top_types_str = ", ".join([f"{ext}({count})" for ext, count in info['top_non_python_types']]) if info['top_non_python_types'] else "None"
        if len(top_types_str) > 38:
            top_types_str = top_types_str[:35] + "..."
        
        print(f"{package:<30} {info['size_mb']:>8.2f} MB   {info['python_size_mb']:>8.2f} MB   {info['total_files']:>8d}     {info['python_files']:>8d}       {top_types_str:<40}")

    
    return result

if __name__ == '__main__':
    create_uv_venv(
        requirements_path='user_input/requirements_demo.txt',
        venv_name='demo_env'
    )

    measure_venv_size('layerlite_env/demo_env')
