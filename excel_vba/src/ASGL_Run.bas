
Attribute VB_Name = "ASGL_Run"
Option Explicit

Public Sub ASGL_OpenRunnerUI()
    RunnerShow
End Sub

Public Sub ASGL_Run()
    On Error GoTo EH
    LogInfo "run_start", "User invoked ASGL Run."
    RunnerShow

    EnsureApiKey
    EnsureTenantId

    Dim apiKey As String: apiKey = GetSetting("api_key", "")
    If Len(apiKey) = 0 Then Exit Sub

    Dim xlsxPath As String
    xlsxPath = PickFile("Select Spreadsheet", "Excel Files", "*.xlsx;*.xlsm;*.xls")
    If Len(xlsxPath) = 0 Then
        LogWarn "run_cancel", "No spreadsheet selected."
        RunnerUpdate "cancelled", 0
        Exit Sub
    End If

    Dim pdfPath As String
    pdfPath = PickFile("Select PDF Working Paper", "PDF Files", "*.pdf")
    If Len(pdfPath) = 0 Then
        LogWarn "run_cancel", "No PDF selected."
        RunnerUpdate "cancelled", 0
        Exit Sub
    End If

    Dim mappingJson As String
    mappingJson = AutoMapFromSelection(1) ' optional: auto-map based on current selection

    ' Submit job
    Dim payload As String
    payload = "{""spreadsheet_path"":""" & EscapeJson(xlsxPath) & """,""pdf_path"":""" & EscapeJson(pdfPath) & """,""source"":""excel_vba"",""telemetry"":""" & EscapeJson(BuildTelemetryContext()) & """,""mapping"":" & mappingJson & "}"

    RunnerUpdate "submitting", 0

    Dim resp As String
    resp = HttpRequest("POST", ASGL_BASE_URL & ASGL_JOB_SUBMIT_PATH, payload, apiKey)
    If Len(resp) = 0 Then
        MsgBox "Failed to submit job. Check diagnostics.", vbExclamation, "ASGL"
        RunnerUpdate "submit_failed", 0
        Exit Sub
    End If

    Dim jobId As String: jobId = JsonGetString(resp, "job_id", "")
    If Len(jobId) = 0 Then jobId = JsonGetString(resp, "id", "")
    If Len(jobId) = 0 Then
        MsgBox "Job submission returned no job_id.", vbExclamation, "ASGL"
        LogError "job_submit_no_id", resp
        RunnerUpdate "submit_no_job_id", 0
        Exit Sub
    End If
    SetSetting "last_job_id", jobId
    LogInfo "job_submitted", "job_id=" & jobId
    RunnerUpdate "submitted", 0, jobId

    ' Poll
    Dim startT As Date: startT = Now
    Dim done As Boolean: done = False
    Dim status As String, progress As Double
    Do While Not done
        Dim stResp As String
        stResp = HttpRequest("GET", ASGL_BASE_URL & ASGL_JOB_STATUS_PATH & jobId, "", apiKey)
        If Len(stResp) > 0 Then
            status = JsonGetString(stResp, "status", "unknown")
            progress = JsonGetNumber(stResp, "progress", 0)
            RunnerUpdate status, progress, jobId
            Application.StatusBar = "ASGL-A: " & status & " (" & CInt(progress * 100) & "%)"
            LogInfo "job_poll", "status=" & status & " progress=" & progress, jobId
            If status = "completed" Or status = "failed" Then done = True
        End If
        DoEvents
        If DateDiff("s", startT, Now) > POLL_MAX_SECONDS Then
            LogWarn "poll_timeout", "Timed out waiting for job completion.", jobId
            Exit Do
        End If
        Dim waitUntil As Date: waitUntil = DateAdd("s", POLL_INTERVAL_SECONDS, Now)
        Do While Now < waitUntil
            DoEvents
        Loop
    Loop
    Application.StatusBar = False

    If status = "completed" Then
        Dim reportUrl As String
        reportUrl = ASGL_BASE_URL & ASGL_JOB_REPORT_PATH & jobId
        LogInfo "job_completed", "Opening report: " & reportUrl, jobId
        ThisWorkbook.FollowHyperlink reportUrl
        RunnerUpdate "completed", 1, jobId
        MsgBox "ASGL-A completed. Report opened.", vbInformation, "ASGL"
    ElseIf status = "failed" Then
        RunnerUpdate "failed", progress, jobId
        MsgBox "ASGL-A failed. Export diagnostics and contact support.", vbExclamation, "ASGL"
        LogError "job_failed", "job_id=" & jobId
    Else
        RunnerUpdate "ended", progress, jobId
        MsgBox "ASGL-A run ended (status=" & status & "). Check log.", vbExclamation, "ASGL"
    End If

    Exit Sub
EH:
    LogError "run_exception", Err.Description
    RunnerUpdate "exception", 0
    MsgBox "ASGL error: " & Err.Description, vbExclamation, "ASGL"
End Sub

Private Function EscapeJson(ByVal s As String) As String
    s = Replace(s, "\", "\\")
    s = Replace(s, """", "\""")
    s = Replace(s, vbCrLf, "\n")
    s = Replace(s, vbCr, "\n")
    s = Replace(s, vbLf, "\n")
    EscapeJson = s
End Function
