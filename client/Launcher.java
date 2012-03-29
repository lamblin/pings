import java.io.*;
import java.util.*;

/**
   This class launches an external commands and returns its output (both on
   stdout and stderr) into two List<String>. As it is static, it is also
   thread-safe.


   @author   Steven Pigeon <pigeon@iro.umonteal.ca>
   @version  0.5
   @since    2012-03
*/
public class Launcher
{
 /**
     Launches a command and reports the exit code, as well as the output
     on both stdout and stderr.

     @param args specifies the command and its arguments
     @param stdout_lines returns the output on stdout as a list of strings
     @param stderr_lines returns the output on stderr as a list of strings
     @param max_nb_lines specifies how many lines to capture (extra lines are ignored)

     @return program exit code (or -1 if a runtime exception occurs)
 */
 public static int launch(List<String> args,
                          List<String> stdout_lines,
                          List<String> stderr_lines,
                          int max_nb_lines)
 {
  Process proc;
  try
   {
    proc=Runtime.getRuntime().exec(args.toArray(new String[args.size()]));

    BufferedReader stdout = new BufferedReader(new InputStreamReader(proc.getInputStream()));
    BufferedReader stderr = new BufferedReader(new InputStreamReader(proc.getErrorStream()));
    String line;
    int c;

    c=0; while ((c<max_nb_lines) && (line = stdout.readLine()) != null) { c++; stdout_lines.add(line); }
    c=0; while ((c<max_nb_lines) && (line = stderr.readLine()) != null) { c++; stderr_lines.add(line); }

    stdout.close();
    stderr.close();
    proc.waitFor();
   }

  catch (Exception error)
   {
    //some error occured!
    //error.printStackTrace();
    return -1; // extra bad
   }

  //...else we return the process'
  // return value:
  return proc.exitValue();
 }
};
