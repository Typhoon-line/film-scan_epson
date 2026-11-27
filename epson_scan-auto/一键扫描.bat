@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EPSON Scan 自动扫描

rem === 首次使用：把下面 PY 改成你本机 Python 解释器（需装 pywinauto）===
set PY=python

:menu
cls
echo.
echo  ============================================
echo            EPSON Scan  自动扫描
echo  ============================================
echo.
echo  [必读] 扫描仪须先【物理断电重启】，再第一次打开 EPSON Scan 才正常。
echo         默认只连接已打开的窗口，不会自己启停软件（避免驱动紊乱）。
echo.
echo    0   初始化：选扫描图像存储磁盘（装完 skill 只做一次）
echo    1   rank      排名档 48bit TIFF  -  扫 1 张
echo    2   jpg_social 社媒档 JPEG        -  扫 1 张
echo    3   tiff_post  后期档 48bit TIFF  -  扫 1 张
echo    4   rank       DRY RUN 只设置不扫描（验证控件定位）
echo    5   列出所有方案
echo    6   退出
echo.
set /p C=请选择 [0-6]:

if "%C%"=="0" goto init
if "%C%"=="1" goto rank
if "%C%"=="2" goto social
if "%C%"=="3" goto post
if "%C%"=="4" goto dryrun
if "%C%"=="5" goto list
if "%C%"=="6" exit /b
goto menu

rem === 存储磁盘只在初始化（菜单 0 / --init）时问一次，结果存 config.json ===
rem === 之后各流程直接读配置，命令行可用 --drive D 临时覆盖 ===

:init
"%PY%" run_flow.py --init
goto done

:rank
"%PY%" run_flow.py --flow rank
goto done

:social
"%PY%" run_flow.py --flow jpg_social
goto done

:post
"%PY%" run_flow.py --flow tiff_post
goto done

:dryrun
"%PY%" run_flow.py --flow rank --dry-run
goto done

:list
"%PY%" run_flow.py --list
goto done

:done
echo.
pause
goto menu
