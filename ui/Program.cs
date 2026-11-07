using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class MainWindow : Form {
    string root=AppDomain.CurrentDomain.BaseDirectory, currentJob="";
    Dictionary<string,TextBox> fields=new Dictionary<string,TextBox>();
    JavaScriptSerializer json=new JavaScriptSerializer();
    TextBox log=new TextBox(), start=new TextBox(), end=new TextBox(), keep=new TextBox(), remove=new TextBox();
    Button build=new Button(), cancel=new Button(), resume=new Button();
    Label state=new Label(); CheckBox render=new CheckBox(); Process worker;
    Color ink=Color.FromArgb(228,234,242), panel=Color.FromArgb(28,35,48), accent=Color.FromArgb(70,221,188);
    public MainWindow() {
        Text="DaJoshiResolve — Local editing alpha"; ClientSize=new Size(1050,835);MinimumSize=new Size(1066,874);
        BackColor=Color.FromArgb(17,23,34);ForeColor=ink;Font=new Font("Segoe UI",10);StartPosition=FormStartPosition.CenterScreen;
        if(File.Exists(Path.Combine(root,"DaJoshiResolve.ico")))Icon=new Icon(Path.Combine(root,"DaJoshiResolve.ico"));
        Label title=new Label {Text="DaJoshiResolve",Font=new Font("Segoe UI Semibold",27),Location=new Point(28,18),AutoSize=true,ForeColor=accent};Controls.Add(title);
        Label desc=new Label {Text="Drop footage. Keep your sources. Shape the edit.",Location=new Point(31,72),AutoSize=true};Controls.Add(desc);
        Label badge=new Label{Text="LOCAL ONLY  /  ALPHA",Location=new Point(800,36),AutoSize=true,ForeColor=accent};Controls.Add(badge);
        string[,] rows={{"video","Video / approved composite"},{"audio","Better microphone (optional)"},{"reference","Original camera (silent composite)"},{"shots","Shot list · CSV / JSON"},{"deck","Animation deck · HTML"},{"script","Script · plain text (optional)"},{"transcript","Cached transcript · JSON (optional)"}};
        for(int i=0;i<rows.GetLength(0);i++)AddInput(rows[i,0],rows[i,1],116+i*42);
        AddSmall("Source start (seconds)",start,30,426,145);AddSmall("Source end (blank = all)",end,210,426,170);start.Text="0";
        render.Text="Render review preview";render.Checked=true;render.AutoSize=true;render.Location=new Point(425,451);Controls.Add(render);
        Label ov=new Label{Text="Optional cut overrides · original video seconds · e.g. 12.4-15.8, 32-34",Location=new Point(30,494),AutoSize=true,ForeColor=accent};Controls.Add(ov);
        AddSmall("Always keep",keep,30,521,470);AddSmall("Always remove",remove,530,521,470);
        MakeButton(build,"Build editable draft",30,593,210);build.BackColor=accent;build.ForeColor=Color.FromArgb(17,23,34);build.Click+=(s,e)=>StartRun();
        MakeButton(cancel,"Cancel safely",255,593,150);cancel.Enabled=false;cancel.Click+=(s,e)=>CancelRun();
        MakeButton(resume,"Resume run…",420,593,150);resume.Click+=(s,e)=>ResumeRun();
        var open=new Button();MakeButton(open,"Open run folder",585,593,170);open.Click+=(s,e)=>OpenFolder();
        var help=new Button();MakeButton(help,"Guide / limits",770,593,230);help.Click+=(s,e)=>Process.Start(new ProcessStartInfo(Path.Combine(root,"README.txt")){UseShellExecute=true});
        state.Text="Ready. Premiere import, layout effects and playback still require native verification.";state.Location=new Point(30,645);state.Size=new Size(980,36);state.ForeColor=Color.FromArgb(246,200,108);Controls.Add(state);
        log.Location=new Point(30,685);log.Size=new Size(970,115);log.Multiline=true;log.ReadOnly=true;log.ScrollBars=ScrollBars.Vertical;log.BackColor=panel;log.ForeColor=ink;log.BorderStyle=BorderStyle.FixedSingle;log.Font=new Font("Consolas",9);Controls.Add(log);
        AllowDrop=true;DragEnter+=DropEnter;DragDrop+=(s,e)=>{foreach(string path in (string[])e.Data.GetData(DataFormats.FileDrop))GuessInput(path);};
        FormClosing+=(s,e)=>{if(worker!=null&&!worker.HasExited){e.Cancel=true;CancelRun();state.Text="Cancelling safely. Close the app after the worker stops.";}};
    }
    void AddInput(string key,string label,int y) {
        Controls.Add(new Label{Text=label,Location=new Point(30,y+7),Size=new Size(280,25)});
        TextBox box=new TextBox{Location=new Point(315,y+3),Size=new Size(600,29),BackColor=panel,ForeColor=ink,BorderStyle=BorderStyle.FixedSingle,AllowDrop=true};fields[key]=box;Controls.Add(box);
        box.DragEnter+=DropEnter;box.DragDrop+=(s,e)=>box.Text=((string[])e.Data.GetData(DataFormats.FileDrop))[0];
        Button b=new Button();MakeButton(b,"…",930,y,70);b.Click+=(s,e)=>{using(var dialog=new OpenFileDialog()){dialog.Title=label;if(dialog.ShowDialog()==DialogResult.OK)box.Text=dialog.FileName;}};
    }
    void AddSmall(string label,TextBox box,int x,int y,int width) {
        Controls.Add(new Label{Text=label,Location=new Point(x,y),AutoSize=true});box.Location=new Point(x,y+25);box.Size=new Size(width,28);box.BackColor=panel;box.ForeColor=ink;Controls.Add(box);
    }
    void MakeButton(Button b,string text,int x,int y,int width) {b.Text=text;b.Location=new Point(x,y);b.Size=new Size(width,36);b.FlatStyle=FlatStyle.Flat;b.BackColor=panel;b.ForeColor=ink;b.FlatAppearance.BorderColor=Color.FromArgb(64,77,94);Controls.Add(b);}
    void DropEnter(object sender,DragEventArgs e){if(e.Data.GetDataPresent(DataFormats.FileDrop))e.Effect=DragDropEffects.Copy;}
    public void GuessInput(string path) {
        string ext=Path.GetExtension(path).ToLowerInvariant();
        if(new[]{".mp4",".mov",".mxf",".mkv",".avi"}.Contains(ext))fields["video"].Text=path;
        else if(new[]{".wav",".flac",".mp3",".m4a"}.Contains(ext))fields["audio"].Text=path;
        else if(new[]{".html",".htm"}.Contains(ext))fields["deck"].Text=path;
        else if(ext==".csv")fields["shots"].Text=path;
        else if(ext==".txt")fields["script"].Text=path;
        else state.Text="Drop JSON into its labelled field so transcript and shot-list files are not confused.";
    }
    List<double[]> Ranges(string text) {
        var list=new List<double[]>();if(String.IsNullOrWhiteSpace(text))return list;
        foreach(string item in text.Split(',')){string[] ab=item.Trim().Split('-');double a,b;if(ab.Length!=2||!Double.TryParse(ab[0],System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out a)||!Double.TryParse(ab[1],System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out b)||a<0||b<=a)throw new Exception("Use ranges such as 12.4-15.8, 32-34.");list.Add(new[]{a,b});}return list;
    }
    void StartRun() {
        try {
            if(String.IsNullOrWhiteSpace(fields["video"].Text))throw new Exception("Drop a video into the first field.");
            var cfg=new Dictionary<string,object>();foreach(var f in fields){if(!String.IsNullOrWhiteSpace(f.Value.Text)){string p=Path.GetFullPath(f.Value.Text.Trim().Trim('"'));if(!File.Exists(p))throw new Exception("File not found: "+p);cfg[f.Key]=p;}}
            double value;if(!Double.TryParse(start.Text,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out value))throw new Exception("Source start must be seconds.");cfg["start"]=value;
            if(!String.IsNullOrWhiteSpace(end.Text)){if(!Double.TryParse(end.Text,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out value))throw new Exception("Source end must be seconds.");cfg["end"]=value;}
            cfg["preview"]=render.Checked;cfg["overrides_inline"]=new Dictionary<string,object>{{"keep",Ranges(keep.Text)},{"remove",Ranges(remove.Text)}};
            string id=DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,6);string queue=Path.Combine(root,"Requests");Directory.CreateDirectory(queue);
            string request=Path.Combine(queue,id+".json");File.WriteAllText(request,json.Serialize(cfg),new UTF8Encoding(false));currentJob=Path.Combine(root,"Runs",id);Launch("--request "+Quote(request)+" --job "+Quote(currentJob));
        }catch(Exception ex){MessageBox.Show(this,ex.Message,"Cannot start",MessageBoxButtons.OK,MessageBoxIcon.Information);}
    }
    static string Quote(string s){return "\""+s.Replace("\"","\\\"")+"\"";}
    void Launch(string arguments) {
        var settings=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"settings.json")));string python=(string)settings["python"];
        if(!File.Exists(python))throw new Exception("Configured Python runtime is missing. Update settings.json; the app does not download software.");
        var pi=new ProcessStartInfo(python,Quote(Path.Combine(root,"worker.py"))+" "+arguments){WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
        pi.EnvironmentVariables["PYTHONUTF8"]="1";pi.EnvironmentVariables["HF_HUB_OFFLINE"]="1";pi.EnvironmentVariables["TRANSFORMERS_OFFLINE"]="1";pi.EnvironmentVariables["OMP_NUM_THREADS"]="2";
        worker=new Process{StartInfo=pi,EnableRaisingEvents=true};worker.OutputDataReceived+=(s,e)=>Append(e.Data);worker.ErrorDataReceived+=(s,e)=>Append(e.Data);
        worker.Exited+=(s,e)=>{if(!IsDisposed)BeginInvoke((Action)(()=>{build.Enabled=true;resume.Enabled=true;cancel.Enabled=false;try{var result=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(currentJob,"status.json")));state.Text=Convert.ToString(result["message"]);}catch{state.Text="Worker stopped. Open run folder for logs.";} }));};
        log.Clear();worker.Start();worker.BeginOutputReadLine();worker.BeginErrorReadLine();build.Enabled=false;resume.Enabled=false;cancel.Enabled=true;state.Text="Working locally. Keep OBS closed while media stages run.";
    }
    void Append(string line){if(line==null||IsDisposed)return;BeginInvoke((Action)(()=>{if(log.TextLength>60000)log.Clear();log.AppendText(line+Environment.NewLine);state.Text=line.Length>145?line.Substring(0,145):line;}));}
    void CancelRun(){if(Directory.Exists(currentJob)){File.WriteAllText(Path.Combine(currentJob,"CANCEL"),"Cancel requested by user");state.Text="Cancellation requested. Finishing the current safe boundary…";cancel.Enabled=false;}}
    void ResumeRun(){try{using(var d=new OpenFileDialog{Filter="Run request|request.json",InitialDirectory=Path.Combine(root,"Runs"),Title="Select request.json inside an interrupted run"}){if(d.ShowDialog()!=DialogResult.OK)return;currentJob=Path.GetDirectoryName(d.FileName);Launch("--job "+Quote(currentJob)+" --resume");}}catch(Exception ex){MessageBox.Show(ex.Message,"Cannot resume");}}
    void OpenFolder(){string p=Directory.Exists(currentJob)?currentJob:Path.Combine(root,"Runs");Directory.CreateDirectory(p);Process.Start(new ProcessStartInfo(p){UseShellExecute=true});}
    public void SelfTest(string output) {
        GuessInput("C:\\fixture.mp4");if(fields["video"].Text!="C:\\fixture.mp4")throw new Exception("Drop routing failed");
        if(Ranges("1.2-3.4, 8-9").Count!=2)throw new Exception("Override parsing failed");fields["video"].Clear();
        ShowInTaskbar=false;Opacity=0;Show();PerformLayout();foreach(Control child in Controls)child.CreateControl();
        using(var bitmap=new Bitmap(Width,Height)){DrawToBitmap(bitmap,new Rectangle(0,0,Width,Height));bitmap.Save(output);}Hide();
        File.WriteAllText(Path.ChangeExtension(output,".json"),"{\"form_constructed\":true,\"drop_routing_tested\":true,\"override_parser_tested\":true,\"manual_drag_drop_tested\":false}");
    }
    [STAThread]static void Main(string[] args){Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);try{var form=new MainWindow();if(args.Length==2&&args[0]=="--self-test"){form.SelfTest(args[1]);form.Dispose();return;}foreach(string arg in args)if(File.Exists(arg))form.GuessInput(arg);Application.Run(form);}catch(Exception e){File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"launcher-error.txt"),e.ToString());Environment.ExitCode=1;}}
}
