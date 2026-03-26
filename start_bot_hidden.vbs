Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\Users\VICTUS\Desktop\swingbot-main && python run.py --lang en > logs\output.log 2>&1", 0, False
