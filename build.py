
import os
import subprocess
import re
import sys
import zipfile
import shutil

def get_version():
    """Extract version from web_server.py"""
    with open('movie_manager/web_server.py', 'r', encoding='utf-8') as f:
        content = f.read()
        match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
        if match:
            return match.group(1)
    return "1.0.0.0"

def create_version_file(version):
    """Generates version_info.txt for PyInstaller"""
    # Windows version needs to be tuple-like: 1,0,0,0
    v_parts = version.split('.')
    while len(v_parts) < 4:
        v_parts.append('0')
    v_tuple = ", ".join(v_parts[:4])
    
    content = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v_tuple}),
    prodvers=({v_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'DuplicateChecker'),
        StringStruct(u'FileDescription', u'Duplicate Media Checker'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'InternalName', u'DuplicateChecker'),
        StringStruct(u'LegalCopyright', u'Copyright (c) 2024'),
        StringStruct(u'OriginalFilename', u'DuplicateChecker.exe'),
        StringStruct(u'ProductName', u'Duplicate Media Checker'),
        StringStruct(u'ProductVersion', u'{version}')])
      ]), 
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    with open('version_info.txt', 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Generated version_info.txt with version {version}")

def create_zip_package(version):
    """Creates a zip package with exe, ff_res contents, and readme"""
    exe_name = f"DuplicateChecker_v{version}.exe"
    exe_path = os.path.join("dist", exe_name)
    zip_name = f"DuplicateChecker_v{version}.zip"
    zip_path = os.path.join("dist", zip_name)
    
    print(f"Creating zip package: {zip_path}...")
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. Add Executable
        if os.path.exists(exe_path):
            zf.write(exe_path, arcname=exe_name)
            print(f"Added {exe_name}")
        else:
            print(f"Error: Executable not found at {exe_path}")
            return

        # 2. Add ff_res contents (ffmpeg, ffprobe, etc.)
        ff_res_dir = "ff_res"
        if os.path.exists(ff_res_dir):
            for root, dirs, files in os.walk(ff_res_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Add to zip root (ignore ff_res folder structure if it's flat, or preserve relative to ff_res)
                    # User said "exe and ff_res files together", usually means flat structure for easy run
                    # So we place them next to exe.
                    zf.write(file_path, arcname=file)
            print(f"Added files from {ff_res_dir}")
        else:
            print(f"Warning: {ff_res_dir} directory not found. ffmpeg dependencies will be missing in zip.")

        # 3. Add README
        readme_src = "README_DEPLOY.md"
        if os.path.exists(readme_src):
            zf.write(readme_src, arcname="README.md")
            print("Added README.md")
    
    print(f"Zip package created successfully at {zip_path}")

def build():
    version = get_version()
    print(f"Building version: {version}")
    
    create_version_file(version)
    
    # Use python -m PyInstaller to avoid PATH issues
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", f"DuplicateChecker_v{version}",
        "--add-data", "movie_manager/templates;templates",
        "--version-file", "version_info.txt",
        "movie_manager/web_server.py"
    ]
    
    print("Running PyInstaller...")
    subprocess.run(cmd, check=True)
    print("PyInstaller build complete!")
    
    create_zip_package(version)

if __name__ == "__main__":
    build()
