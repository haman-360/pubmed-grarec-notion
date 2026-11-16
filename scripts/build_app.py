"""Build the small native window using the macOS toolchain; no extra Python packages."""
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'PubMed-grareko-notion.app'

def build_icon(contents):
    source=ROOT/'assets/app-icon.png'
    icon=contents/'Resources/AppIcon.icns'
    if not icon.exists() or source.stat().st_mtime>icon.stat().st_mtime:
        icon.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary:
            iconset=Path(temporary)/'AppIcon.iconset'
            iconset.mkdir()
            for size in (16,32,128,256,512):
                for scale in (1,2):
                    pixels=size*scale
                    suffix='@2x' if scale==2 else ''
                    destination=iconset/f'icon_{size}x{size}{suffix}.png'
                    subprocess.run(['sips','-z',str(pixels),str(pixels),str(source),'--out',str(destination)],
                                   check=True,stdout=subprocess.DEVNULL)
            subprocess.run(['iconutil','-c','icns',str(iconset),'-o',str(icon)],check=True)
        APP.touch()

def build():
    contents=APP/'Contents'; binary=contents/'MacOS/PubMedGraRec'
    binary.parent.mkdir(parents=True,exist_ok=True)
    source=ROOT/'scripts/GrarecApp.swift'
    if not binary.exists() or source.stat().st_mtime>binary.stat().st_mtime:
        cache=ROOT/'.build/swift-app-cache'; cache.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,CLANG_MODULE_CACHE_PATH=str(cache))
        subprocess.run(['swiftc',str(source),'-o',str(binary),'-framework','Cocoa','-framework','WebKit'],env=env,check=True)
    build_icon(contents)
    with (contents/'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable':'PubMedGraRec','CFBundleIdentifier':'local.pubmed.grarecnotion',
                       'CFBundleName':'PubMed GraRec Notion','CFBundlePackageType':'APPL',
                       'CFBundleIconFile':'AppIcon.icns','NSHighResolutionCapable':True},f)
    return APP
if __name__=='__main__': print(build())
