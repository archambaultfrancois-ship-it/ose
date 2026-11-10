with Interfaces;
package body Probe_Callbacks is
   use type Interfaces.Unsigned_64;
   use type Interfaces.IEEE_Float_64;
   protected body Received is
      procedure Signal is begin N := N + 1; end;
      function Count return Natural is (N);
   end Received;
   procedure Receive (Message : SDL_measurement.T_Measurement; Info : Ose_Client.Message_Info; User : System.Address) is
   begin
      if Info.Sequence <= 2**63 or Message.F_sequence /= Interfaces.Unsigned_64'Last or Message.F_value /= 3.5 then
         raise Program_Error with "incorrect decoded data";
      end if;
      if Fail then raise Program_Error with "expected callback failure" & Character'Val (0); end if;
      Received.Signal;
   end Receive;
   procedure Receive_Id (Message : SDL_measurement.T_Measurement; Info : Ose_Client.Message_Info; User : System.Address) is
      Expected : Interfaces.Unsigned_64 := 0;
   begin
      case Message.F_sequence is
         when 0 => null;
         when 1 => Expected := 0;
         when 2 => Expected := 2**53 + 1;
         when 3 => Expected := Interfaces.Unsigned_64'Last;
         when others => raise Program_Error with "invalid ID test message";
      end case;
      if Info.Has_App_Id /= (Message.F_sequence /= 0) or else
         (Info.Has_App_Id and then Info.App_Id /= Expected) then
         raise Program_Error with "incorrect application ID";
      end if;
      Received.Signal;
   end Receive_Id;
end Probe_Callbacks;
