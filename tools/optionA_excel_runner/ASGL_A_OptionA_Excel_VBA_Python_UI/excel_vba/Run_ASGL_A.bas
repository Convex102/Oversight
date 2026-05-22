Attribute VB_Name = "RunASGLA"
Option Explicit

' === ASGL-A Runner Launcher ===
' This macro launches the ASGL-A desktop runner and passes the active workbook.
'
' Update the paths below:
' 1) ASGL_EXE_OR_PYTHON: either python.exe OR the compiled asgl_runner.exe
' 2) ASGL_ENTRYPOINT: the runner entrypoint script if using python (runner_app.py)
'
' Recommended for production: compile the python runner to EXE and set ASGL_ENTRYPOINT="".
'
Sub Run_ASGL_A()

    Dim ASGL_EXE_OR_PYTHON As String
    Dim ASGL_ENTRYPOINT As String
    Dim workbookPath As String
    Dim args As String
    Dim cmd As String

    ' === OPTION 1: Use python.exe (dev) ===
    ASGL_EXE_OR_PYTHON = "C:\Users\nutta\AppData\Local\Programs\Python\Python312\python.exe"
    ASGL_ENTRYPOINT = "C:\ASGL\ASGL_A_OptionA_Excel_VBA_Python_UI\asgl_a\ui\runner_app.py"

    ' === OPTION 2: Use compiled EXE (prod) ===
    'ASGL_EXE_OR_PYTHON = "C:\ASGL\dist\asgl_runner.exe"
    'ASGL_ENTRYPOINT = ""

    workbookPath = ActiveWorkbook.FullName

    ' Pass flags:
    ' --file "<path>"
    ' --compare
    args = " --file """ & workbookPath & """ --compare"

    If ASGL_ENTRYPOINT <> "" Then
        cmd = """" & ASGL_EXE_OR_PYTHON & """ """ & ASGL_ENTRYPOINT & """" & args
    Else
        cmd = """" & ASGL_EXE_OR_PYTHON & """" & args
    End If

    Shell cmd, vbNormalFocus

End Sub
