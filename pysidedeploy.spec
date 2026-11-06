[app]
title = OSV360Craft
project_dir = .
input_file = launcher.py
exec_directory = dist
project_file = 
icon = assets/OSV360Craft.ico

[python]
packages = Nuitka==4.3.4, ziglang==0.16.0
android_packages = 

[qt]
qml_files = 
excluded_qml_plugins = 
modules = Core,Gui,Widgets,OpenGL,OpenGLWidgets
plugins = accessiblebridge,egldeviceintegrations,generic,iconengines,imageformats,platforminputcontexts,platforms,platforms/darwin,platformthemes,styles,xcbglintegrations

[nuitka]
mode = onefile
extra_args = --quiet --noinclude-qt-translations --zig --windows-console-mode=disable --windows-icon-from-ico=assets/OSV360Craft.ico --include-data-dir=assets=assets --include-data-dir=config=config --include-package=psutil --windows-company-name=PitKub --windows-product-name=OSV360Craft --windows-file-description=OSV360Craft --windows-product-version=1.0.0.0 --windows-file-version=1.0.0.0
