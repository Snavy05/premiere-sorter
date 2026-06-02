' SteadyCut — Windows launcher
' Double-click to run. No command prompt window on normal launch.
' On first run it opens a setup window, then launches the app automatically.

Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = scriptDir

' Use python.exe (not pythonw.exe) with a hidden window.
' pythonw.exe sets sys.stdout/stderr to None which breaks logging.
python  = scriptDir & "\.venv\Scripts\python.exe"
runPy   = scriptDir & "\run.py"

If fso.FileExists(python) Then
    ' Normal launch — window style 0 = hidden, no cmd window visible
    ws.Run Chr(34) & python & Chr(34) & " " & Chr(34) & runPy & Chr(34), 0, False
Else
    ' First run — show setup window so user can see progress
    MsgBox "SteadyCut is setting up for the first time." & vbCrLf & vbCrLf & _
           "A setup window will open. This takes 2-3 minutes." & vbCrLf & _
           "The app will launch automatically when done.", _
           vbInformation Or vbOKOnly, "SteadyCut"
    ws.Run "cmd /c """ & scriptDir & "\launch.bat""", 1, True
End If
