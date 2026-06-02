' SteadyCut — Windows launcher
' Double-click this file to run SteadyCut with no command prompt window.
' On first run it opens a setup window, then launches the app automatically.

Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = scriptDir

pythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
runPy   = scriptDir & "\run.py"

If fso.FileExists(pythonw) Then
    ' Normal launch — no console window
    ws.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & runPy & Chr(34), 0, False
Else
    ' First run — show setup window so user can see progress
    MsgBox "SteadyCut is setting up for the first time." & vbCrLf & vbCrLf & _
           "A setup window will open. This takes 2-3 minutes." & vbCrLf & _
           "The app will launch automatically when done.", _
           vbInformation Or vbOKOnly, "SteadyCut"
    ws.Run "cmd /c """ & scriptDir & "\launch.bat""", 1, True
End If
