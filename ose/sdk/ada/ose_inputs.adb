with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Ose_Text;
package body Ose_Inputs is
   use type Interfaces.Unsigned_8;
   use type Interfaces.Unsigned_64;
   use Ose_Component;
   Max_Body : constant := 1024 * 1024 - 1;
   function Number (Data : SDL_Runtime.Bytes; Position : in out Natural; Width : Positive)
     return Interfaces.Unsigned_64 is
      Value : Interfaces.Unsigned_64 := 0;
   begin
      if Width > 8 or else Position > Data'Length or else Width > Data'Length - Position then
         raise Protocol_Error with "truncated CH/2 number";
      end if;
      for I in 1 .. Width loop
         Value := Interfaces.Shift_Left (Value, 8) or Interfaces.Unsigned_64 (Data (Data'First + Position));
         Position := Position + 1;
      end loop;
      return Value;
   end Number;
   function Flag (Data : SDL_Runtime.Bytes; Position : in out Natural) return Boolean is
      Value : constant Interfaces.Unsigned_64 := Number (Data, Position, 1);
   begin
      if Value > 1 then raise Protocol_Error with "invalid CH/2 presence flag"; end if;
      return Value = 1;
   end Flag;
   procedure At_End (Data : SDL_Runtime.Bytes; Position : Natural) is
   begin
      if Position /= Data'Length then raise Protocol_Error with "trailing CH/2 bytes"; end if;
   end At_End;
   function Octets (Data : SDL_Runtime.Bytes) return String is
      Text : String (1 .. Data'Length);
   begin
      for I in Text'Range loop Text (I) := Character'Val (Data (Data'First + I - 1)); end loop;
      if not Ose_Text.Valid_UTF8 (Text) then raise Protocol_Error with "invalid CH/2 UTF-8"; end if;
      return Text;
   end Octets;
   function Text (Data : SDL_Runtime.Bytes; Position : in out Natural; Wide : Boolean := False)
     return Unbounded_String is
      Size : constant Interfaces.Unsigned_64 := Number (Data, Position, (if Wide then 4 else 2));
      Start : constant Natural := Position;
   begin
      if Size = 0 or else Size > Interfaces.Unsigned_64 (Data'Length - Position) then
         raise Protocol_Error with "invalid CH/2 text size";
      end if;
      Position := Position + Natural (Size);
      return To_Unbounded_String (Octets (Data (Data'First + Start .. Data'First + Position - 1)));
   end Text;
   function Identity (Data : SDL_Runtime.Bytes; Position : in out Natural) return Optional_Id is
      Result : Optional_Id;
   begin
      Result.Present := Flag (Data, Position);
      if Result.Present then Result.Value := Number (Data, Position, 8); end if;
      return Result;
   end Identity;
   function Read_Record (Data : SDL_Runtime.Bytes; Position : in out Natural) return Message_Record is
      Result : Message_Record;
   begin
      Result.Present := Flag (Data, Position);
      if Result.Present then
         Result.Handle := Text (Data, Position); Result.Source := Text (Data, Position);
         Result.App_Id := Identity (Data, Position);
         Result.Received_Ns := Number (Data, Position, 8); Result.Catalogue := Text (Data, Position, True);
         if Position = Data'Length then raise Protocol_Error with "empty CH/2 envelope"; end if;
         while Position < Data'Length loop
            Result.Envelope.Append (Data (Data'First + Position)); Position := Position + 1;
         end loop;
      end if;
      At_End (Data, Position); return Result;
   end Read_Record;
   function Record_Metadata (Port : String; Item : Message_Record) return Metadata is
   begin
      return (Port => To_Unbounded_String (Port), Handle => Item.Handle, Source => Item.Source,
              Kind => Message_Event, App_Id => Item.App_Id, Elapsed => 1, Due_Ns => Item.Received_Ns);
   end Record_Metadata;
   function Parse_Activation (E : Ose_Component.Event) return Activation is
      Data : constant SDL_Runtime.Bytes := Body_Bytes (E);
      Position : Natural := 0;
      Code : Interfaces.Unsigned_64;
      Result : Activation;
   begin
      if Op (E) /= 'T' or else Data'Length > Max_Body then raise Protocol_Error with "expected CH/2 activation"; end if;
      Result.Time_Ns := Number (Data, Position, 8); Result.Index := Number (Data, Position, 8);
      Result.Meta.Due_Ns := Number (Data, Position, 8); Result.Meta.Elapsed := Number (Data, Position, 8);
      Code := Number (Data, Position, 1);
      if Code /= 1 and Code /= 2 then raise Protocol_Error with "invalid CH/2 event"; end if;
      Result.Meta.Kind := (if Code = 1 then Message_Event else Timeout_Event);
      Result.Meta.Port := Text (Data, Position); Result.Item := Read_Record (Data, Position);
      if Result.Index = 0 or else Result.Meta.Elapsed = 0 or else Result.Meta.Due_Ns > Result.Time_Ns or else
         (Code = 1) /= Result.Item.Present or else
         (Result.Item.Present and then (Result.Meta.Elapsed /= 1 or else Result.Item.Received_Ns > Result.Time_Ns)) then
         raise Protocol_Error with "invalid CH/2 activation context";
      end if;
      if Result.Item.Present then
         Result.Meta.Handle := Result.Item.Handle; Result.Meta.Source := Result.Item.Source;
         Result.Meta.App_Id := Result.Item.App_Id;
      end if;
      return Result;
   end Parse_Activation;
   procedure Put_Text (Data : in out SDL_Runtime.Buffer; Value : String) is
   begin
      if Value'Length = 0 or else Value'Length > 65535 or else not Ose_Text.Valid_UTF8 (Value) then
         raise Protocol_Error with "invalid CH/2 text";
      end if;
      SDL_Runtime.Write (Data, Interfaces.Unsigned_16 (Value'Length));
      for C of Value loop Data.Append (Interfaces.Unsigned_8 (Character'Pos (C))); end loop;
   end Put_Text;
   procedure Put_Id (Data : in out SDL_Runtime.Buffer; Value : Optional_Id) is
   begin
      Data.Append (if Value.Present then 1 else 0);
      if Value.Present then SDL_Runtime.Write (Data, Value.Value); end if;
   end Put_Id;
   procedure Ready is
      Data : constant SDL_Runtime.Bytes := (1 => 2);
   begin
      Send_Event ('R', Data);
   end Ready;
   procedure Publish (Port : String; Payload : SDL_Runtime.Bytes; Id : Optional_Id := No_Id) is
      Data : SDL_Runtime.Buffer;
   begin
      if Payload'Length = 0 or else Payload'Length > Max_Body then raise Protocol_Error with "invalid CH/2 payload size"; end if;
      Put_Text (Data, Port); Put_Id (Data, Id);
      if Natural (Data.Length) > Max_Body - Payload'Length then raise Protocol_Error with "CH/2 publication exceeds limit"; end if;
      for B of Payload loop Data.Append (B); end loop;
      Send_Event ('P', SDL_Runtime.To_Bytes (Data));
   end Publish;
   function Reply (Request : SDL_Runtime.Buffer) return SDL_Runtime.Bytes is
      E : Ose_Component.Event;
   begin
      Send_Event ('Q', SDL_Runtime.To_Bytes (Request)); Read_Event (E);
      declare Data : constant SDL_Runtime.Bytes := Body_Bytes (E);
      begin
         if Op (E) /= 'A' or else Data'Length = 0 then raise Protocol_Error with "invalid CH/2 reply"; end if;
         if Data (Data'First) = 0 then
            if Data'Length > 4097 then raise Protocol_Error with "CH/2 error exceeds limit"; end if;
            raise Protocol_Error with Octets (Data (Data'First + 1 .. Data'Last));
         end if;
         if Data (Data'First) /= 1 then raise Protocol_Error with "invalid CH/2 reply status"; end if;
         return Data (Data'First + 1 .. Data'Last);
      end;
   end Reply;
   function Request_Message (Operation : Interfaces.Unsigned_8; Port : String; Selection : Selector) return SDL_Runtime.Bytes is
      Data : SDL_Runtime.Buffer;
      Bits : Interfaces.Unsigned_8 := 0;
   begin
      Data.Append (Operation); Put_Text (Data, Port);
      if Selection.App_Id.Present then Bits := Bits or 1; end if;
      if Length (Selection.Handle) > 0 then Bits := Bits or 2; end if;
      if Length (Selection.Source) > 0 then Bits := Bits or 4; end if;
      Data.Append (Bits);
      if Selection.App_Id.Present then SDL_Runtime.Write (Data, Selection.App_Id.Value); end if;
      if Length (Selection.Handle) > 0 then Put_Text (Data, To_String (Selection.Handle)); end if;
      if Length (Selection.Source) > 0 then Put_Text (Data, To_String (Selection.Source)); end if;
      return Reply (Data);
   end Request_Message;
   function Get (Port : String; Selection : Selector := Any_Message; Consume : Boolean := False) return Message_Record is
      Data : constant SDL_Runtime.Bytes := Request_Message ((if Consume then 2 else 1), Port, Selection);
      Position : Natural := 0;
   begin
      return Read_Record (Data, Position);
   end Get;
   function Remove (Port : String; Selection : Selector := Any_Message) return Boolean is
      Data : constant SDL_Runtime.Bytes := Request_Message (3, Port, Selection);
      Position : Natural := 0;
      Result : constant Boolean := Flag (Data, Position);
   begin
      At_End (Data, Position); return Result;
   end Remove;
   procedure Control_Timer (Operation : Interfaces.Unsigned_8; Port : String; Delay_Ns : Optional_Id) is
      Data : SDL_Runtime.Buffer;
   begin
      Data.Append (Operation); Put_Text (Data, Port);
      if Operation = 4 then
         if Delay_Ns.Present and then Delay_Ns.Value = 0 then raise Protocol_Error with "timer delay must be positive"; end if;
         Put_Id (Data, Delay_Ns);
      end if;
      declare Result : constant SDL_Runtime.Bytes := Reply (Data);
      begin if Result'Length /= 0 then raise Protocol_Error with "invalid timer reply"; end if; end;
   end Control_Timer;
   procedure Arm (Port : String; Delay_Ns : Optional_Id := No_Id) is
   begin Control_Timer (4, Port, Delay_Ns); end Arm;
   procedure Disarm (Port : String) is
   begin Control_Timer (5, Port, No_Id); end Disarm;
end Ose_Inputs;
