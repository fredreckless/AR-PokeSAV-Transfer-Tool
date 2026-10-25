import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.*;
import ghidra.app.cmd.disassemble.*;
import ghidra.app.cmd.function.*;
import java.math.BigInteger;
public class SeedThumb extends GhidraScript {
  public void run() throws Exception {
    Register tm = currentProgram.getRegister("TMode");
    long start = 0x08000100L, end = 0x08010000L;
    int n = 0;
    for (long a = start; a < end; a += 2) {
      Address ad = toAddr(a);
      if (getInstructionContaining(ad) != null) continue;
      int hw = (getByte(ad) & 0xff) | ((getByte(ad.add(1)) & 0xff) << 8);
      if ((hw & 0xff00) == 0xb500) {   // push {...,lr}
        currentProgram.getProgramContext().setValue(tm, ad, ad, BigInteger.ONE);
        new DisassembleCommand(ad, null, true).applyTo(currentProgram, monitor);
        if (getFunctionAt(ad) == null) new CreateFunctionCmd(ad).applyTo(currentProgram, monitor);
        n++;
      }
    }
    println("seeded " + n);
  }
}
