with Ose_Component;
package body Ose_Measurements is
   use type Ada.Containers.Count_Type;
   procedure Declare_Measurement (R : in out Registry; Name : String) is
      First : Boolean := True;
   begin
      if R.Frozen or else R.Names.Contains (Name) or else R.Names.Length >= 128 or else
         Name'Length = 0 or else Name'Length > 200 then
         raise Program_Error with "invalid measurement declaration";
      end if;
      for C of Name loop
         if C = '.' then
            if First then raise Program_Error with "empty measurement name segment"; end if;
            First := True;
         else
            if not (C in 'a' .. 'z' or else C in 'A' .. 'Z' or else C = '_' or else
                    (not First and then C in '0' .. '9')) then
               raise Program_Error with "invalid measurement name";
            end if;
            First := False;
         end if;
      end loop;
      if First then raise Program_Error with "empty measurement name segment"; end if;
      Ose_Component.Declare_Measurement (Name);
      R.Names.Insert (Name);
   end Declare_Measurement;
   procedure Measure (R : Registry; Name : String; Value : SDL_Runtime.Float64) is
   begin
      if not R.Names.Contains (Name) then raise Program_Error with "unknown measurement name"; end if;
      if not Value'Valid then raise Program_Error with "measurement requires a finite fl64"; end if;
      Ose_Component.Measure (Name, Value);
   end Measure;
end Ose_Measurements;
