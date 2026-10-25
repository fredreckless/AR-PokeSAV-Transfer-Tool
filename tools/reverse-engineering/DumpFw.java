import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import java.io.*;
public class DumpFw extends GhidraScript {
  public void run() throws Exception {
    // seed: ARM entry at 0x08000000 and thumb code discovered by aggressive analysis
    DecompInterface di = new DecompInterface();
    di.openProgram(currentProgram);
    PrintWriter out = new PrintWriter(new FileWriter("fw.c"));
    for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
      DecompileResults r = di.decompileFunction(f, 60, monitor);
      out.println("// ===== " + f.getName() + " @ " + f.getEntryPoint());
      if (r != null && r.getDecompiledFunction() != null) out.println(r.getDecompiledFunction().getC());
    }
    out.close();
    // also dump string xrefs
    PrintWriter x = new PrintWriter(new FileWriter("xrefs.txt"));
    ReferenceManager rm = currentProgram.getReferenceManager();
    for (Data d : currentProgram.getListing().getDefinedData(true)) {
      if (!d.hasStringValue()) continue;
      for (Reference ref : rm.getReferencesTo(d.getAddress())) {
        Function f = getFunctionContaining(ref.getFromAddress());
        x.println(d.getAddress() + "\t" + (f==null?"?":f.getName()) + "\t" + d.getDefaultValueRepresentation());
      }
    }
    x.close();
  }
}
