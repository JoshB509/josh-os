"""Own application file dialog, shown only after the user presses Browse."""
import sys,tkinter as tk
from tkinter import filedialog
kind=sys.argv[1]
types={'video':[('Video','*.mp4 *.mov *.mkv *.mxf *.avi')],'reference':[('Video','*.mp4 *.mov *.mkv *.mxf *.avi')],'audio':[('Audio','*.wav *.flac *.mp3 *.m4a')],'shots':[('Shot list','*.json *.csv')],'deck':[('HTML deck','*.html *.htm')],'transcript':[('Transcript','*.json')]}
root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
path=filedialog.askdirectory(parent=root,title='Choose this video project folder',mustexist=True) if kind=='folder' else filedialog.askopenfilename(parent=root,title='DaJoshiResolve · Choose '+kind,filetypes=types[kind]+[('All files','*.*')]);root.destroy()
sys.stdout.reconfigure(encoding='utf-8');print(path)
