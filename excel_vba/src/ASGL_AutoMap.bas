
Attribute VB_Name = "ASGL_AutoMap"
Option Explicit

' Creates variables from headers in the row specified and maps each column down to the selection bottom.
' Returns JSON mapping string compatible with template picker API payload.
Public Function AutoMapFromSelection(Optional ByVal headerRow As Long = 1) As String
    On Error GoTo EH
    Dim sel As Range: Set sel = Selection
    Dim area As Range: Set area = sel.Areas(1)
    Dim topRow As Long: topRow = area.Row
    Dim bottomRow As Long: bottomRow = area.Row + area.Rows.Count - 1
    Dim leftCol As Long: leftCol = area.Column
    Dim rightCol As Long: rightCol = area.Column + area.Columns.Count - 1

    Dim hdrRow As Long
    hdrRow = headerRow
    If hdrRow < 1 Then hdrRow = topRow

    Dim i As Long
    Dim vars As String: vars = "["
    For i = leftCol To rightCol
        Dim name As String
        name = CStr(Cells(hdrRow, i).Value)
        If Len(name) = 0 Then name = "Column_" & (i - leftCol + 1)
        Dim startAddr As String: startAddr = Cells(hdrRow, i).Address(False, False)
        Dim endAddr As String: endAddr = Cells(bottomRow, i).Address(False, False)
        vars = vars & "{""name"":""" & EscapeJson(name) & """,""ranges"":[{""name"":""" & EscapeJson(name) & """,""areas"":[{""start"":""" & startAddr & """,""end"":""" & endAddr & """}]}]},"
    Next i
    If Right$(vars, 1) = "," Then vars = Left$(vars, Len(vars) - 1)
    vars = vars & "]"
    AutoMapFromSelection = "{""sheet"":""" & ActiveSheet.Name & """,""variables"":" & vars & "}"
    Exit Function
EH:
    AutoMapFromSelection = "{}"
End Function

Private Function EscapeJson(ByVal s As String) As String
    s = Replace(s, "\", "\\")
    s = Replace(s, """", "\""")
    s = Replace(s, vbCrLf, "\n")
    EscapeJson = s
End Function
