using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;
using System.Windows.Forms;
[assembly:System.Reflection.AssemblyTitle("Josh OS")]
[assembly:System.Reflection.AssemblyProduct("Josh OS")]
class StudioLauncher {
 static string Q(string s){return "\""+s.Replace("\"","\\\"")+"\"";}
 [STAThread] static void Main(string[] args){
  string root=AppDomain.CurrentDomain.BaseDirectory;
  try {
   var cfg=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"settings.json")));
   string python=(string)cfg["python"];
   string script=Path.Combine(root,"studio_app.py");
   if(!File.Exists(python)||!File.Exists(script))throw new Exception("Josh OS runtime is missing. See the local installation guide.");
   if(args.Length==1&&args[0]=="--check"){File.WriteAllText(Path.Combine(root,"studio-launch-check.json"),"{\"runtime_found\":true,\"launcher\":\"0.2.0-studio\"}");return;}
   if(args.Any(File.Exists)){
    Process.Start(new ProcessStartInfo(Path.Combine(root,"DaJoshiResolve.Editor.exe"),string.Join(" ",args.Select(Q))){WorkingDirectory=root,UseShellExecute=false});return;
   }
   Process.Start(new ProcessStartInfo(python,Q(script)){WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden});
  }catch(Exception e){MessageBox.Show(e.Message,"Josh OS",MessageBoxButtons.OK,MessageBoxIcon.Error);Environment.ExitCode=1;}
 }
}
