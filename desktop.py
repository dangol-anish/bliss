"""Desktop actions used by the local web app."""
import os
import subprocess
import sys


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.run(["/usr/bin/open", str(path)], check=True, capture_output=True, timeout=10)


def choose_backup_folder():
    if sys.platform == "win32":
        command = ["powershell.exe", "-NoProfile", "-STA", "-Command", """
            $ErrorActionPreference = 'Stop'
            Add-Type -AssemblyName System.Windows.Forms
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
            $dialog.Description = 'Choose where to move the chapter backup'
            try {
                if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
                    [Console]::Write($dialog.SelectedPath)
                }
            } finally {
                $dialog.Dispose()
            }
        """]
    else:
        command = ["/usr/bin/osascript", "-e", """
            try
                return POSIX path of (choose folder with prompt "Choose where to move the chapter backup")
            on error number -128
                return ""
            end try
        """]
    return subprocess.run(command, check=True, capture_output=True, encoding="utf-8").stdout.rstrip("\r\n")


def play_alert():
    if sys.platform == "win32":
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    else:
        subprocess.Popen(["/usr/bin/afplay", "/System/Library/Sounds/Sosumi.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
