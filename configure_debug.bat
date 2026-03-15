@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cmake -S C:\git\RC_CAR_GUI -B C:\git\RC_CAR_GUI\build -G Ninja -DCMAKE_BUILD_TYPE=Debug -DRC_CAR_USE_CUDA=OFF -DPython_EXECUTABLE=C:\Users\cayod\anaconda3\python.exe
