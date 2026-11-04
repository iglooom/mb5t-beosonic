import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import java.io.*;
// RANGES=lo-hi,... : force-disassemble [lo,hi) linearly (no flow), print listing with bytes
public class DisAt extends GhidraScript { public void run() throws Exception {
  PrintWriter pw=new PrintWriter(new FileWriter(System.getenv("OUT")));
  for(String r:System.getenv("RANGES").split(",")){ String[] a=r.split("-");
    long lo=Long.decode(a[0]),hi=Long.decode(a[1]);
    pw.println("=== "+r);
    long p=lo;
    while(p<hi){ Address A=toAddr(p);
      if(getInstructionAt(A)==null){ clearListing(A); DisassembleCommand c=new DisassembleCommand(new AddressSet(A,A),null,false); c.applyTo(currentProgram,monitor);}
      Instruction i=getInstructionAt(A);
      if(i==null){ pw.println(String.format("%x  ?? %02x", p, getByte(A)&0xff)); p+=2; continue;}
      byte[] b=i.getBytes(); StringBuilder sb=new StringBuilder(); for(byte x:b) sb.append(String.format("%02x",x&0xff));
      pw.println(String.format("%x\t%s\t%s",p,sb,i)); p+=i.getLength(); }
  }
  pw.close();
}}
