"""Build the small native window using the macOS toolchain; no extra Python packages."""
import os
import plistlib
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'PubMed-grareko-notion.app'

def build():
    contents=APP/'Contents'; binary=contents/'MacOS/PubMedGraRec'
    binary.parent.mkdir(parents=True,exist_ok=True)
    source=ROOT/'scripts/GrarecApp.swift'
    if not binary.exists() or source.stat().st_mtime>binary.stat().st_mtime:
        cache=ROOT/'.build/swift-app-cache'; cache.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,CLANG_MODULE_CACHE_PATH=str(cache))
        subprocess.run(['swiftc',str(source),'-o',str(binary),'-framework','Cocoa','-framework','WebKit'],env=env,check=True)
    with (contents/'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable':'PubMedGraRec','CFBundleIdentifier':'local.pubmed.grarecnotion',
                       'CFBundleName':'PubMed GraRec Notion','CFBundlePackageType':'APPL','NSHighResolutionCapable':True},f)
    return APP
if __name__=='__main__': print(build())
