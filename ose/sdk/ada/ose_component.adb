with Ada.Unchecked_Conversion;
with Ose_Text;
with Interfaces.C.Strings;
package body Ose_Component is
   use type Interfaces.C.int;
   function C_Log (Level : Interfaces.C.int; Logger, Message : Interfaces.C.char_array) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_internal_log";
   procedure Log (Level : Log_Level; Logger, Message : String) is
   begin
      if Logger'Length > 200 or else Message'Length = 0 or else
         (for some C of Logger => C = Character'Val (0)) or else
         (for some C of Message => C = Character'Val (0)) then
         raise Protocol_Error with "invalid log category/message";
      end if;
      if C_Log (Interfaces.C.int ((Log_Level'Pos (Level) + 1) * 10),
                Interfaces.C.To_C (Logger), Interfaces.C.To_C (Message)) = 0 then
         raise Protocol_Error with "log deposit failed";
      end if;
   end Log;
   use type Interfaces.C.size_t;
   use type Interfaces.Unsigned_64;
   function C_Read (E : access Raw_Event) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_read";
   procedure C_Release (E : access Raw_Event)
     with Import, Convention => C, External_Name => "ose_release";
   function C_Send (Code : Interfaces.C.char; Data : System.Address; Size : Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_send";
   function C_Record (Name : Interfaces.C.char_array; Data : System.Address; Size : Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_record_bytes";
   function C_Declare_Recording (Name, Root, Catalogue, Description : Interfaces.C.char_array) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_declare_recording";
   function C_Declare_Measurement (Name : Interfaces.C.char_array) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_measurement_declare";
   function C_Measure (Name : Interfaces.C.char_array; Value : Interfaces.C.double) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_measurement_value";
   function C_Input (E : access constant Raw_Event; Port : access System.Address; Port_Size : access Interfaces.C.size_t;
                     Catalogue : access System.Address; Catalogue_Size : access Interfaces.C.size_t;
                     Payload : access System.Address; Payload_Size : access Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_input";
   function C_Set_Parameters (Name : Interfaces.C.char_array; Data : System.Address; Size : Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "ose_set_parameters";
   procedure Check (Result : Interfaces.C.int) is
   begin
      if Result = 0 then raise Protocol_Error with "invalid or incomplete component frame"; end if;
   end Check;
   function Copy_Text (Data : System.Address; Size : Interfaces.C.size_t) return String is
      Bytes : Interfaces.C.char_array (0 .. Size) with Import, Address => Data;
      Result : String (1 .. Natural (Size));
   begin
      for I in Result'Range loop Result (I) := Interfaces.C.To_Ada (Bytes (Interfaces.C.size_t (I - 1))); end loop;
      if not Ose_Text.Valid_UTF8 (Result) then raise Protocol_Error with "invalid UTF-8"; end if;
      -- Strings are UTF-8 octets, as in the public SDL Ada codec.
      return Result;
   end Copy_Text;
   overriding procedure Finalize (E : in out Event) is
   begin C_Release (E.Raw'Access); end Finalize;
   procedure Read_Event (E : in out Event) is
   begin C_Release (E.Raw'Access); Check (C_Read (E.Raw'Access)); end Read_Event;
   function Op (E : Event) return Character is (Interfaces.C.To_Ada (E.Raw.Code));
   function Body_Text (E : Event) return String is (Copy_Text (E.Raw.Data, E.Raw.Size));
   function Body_Size (E : Event) return Natural is (Natural (E.Raw.Size));
   function Body_Bytes (E : Event) return SDL_Runtime.Bytes is
      Data : SDL_Runtime.Bytes (1 .. Natural (E.Raw.Size)) with Import, Address => E.Raw.Data;
   begin
      return Data;
   end Body_Bytes;
   procedure Send_Event (Code : Character; Data : SDL_Runtime.Bytes) is
   begin
      Check (C_Send (Interfaces.C.To_C (Code), Data'Address, Interfaces.C.size_t (Data'Length)));
   end Send_Event;
   procedure Input (E : Event; Port, Catalogue : out Ada.Strings.Unbounded.Unbounded_String;
                    Payload : out SDL_Runtime.Buffer) is
      P, C, D : aliased System.Address;
      PN, CN, DN : aliased Interfaces.C.size_t;
   begin
      Check (C_Input (E.Raw'Access, P'Access, PN'Access, C'Access, CN'Access, D'Access, DN'Access));
      if PN = 0 then raise Protocol_Error with "empty input port"; end if;
      Port := Ada.Strings.Unbounded.To_Unbounded_String (Copy_Text (P, PN));
      Catalogue := Ada.Strings.Unbounded.To_Unbounded_String (Copy_Text (C, CN));
      Payload.Clear;
      declare Bytes : SDL_Runtime.Bytes (1 .. Natural (DN)) with Import, Address => D;
      begin for B of Bytes loop Payload.Append (B); end loop; end;
   end Input;
   procedure Set_Parameters (Name : String; Payload : SDL_Runtime.Bytes) is
   begin
      Check (C_Set_Parameters (Interfaces.C.To_C (Name), Payload'Address, Interfaces.C.size_t (Payload'Length)));
   end Set_Parameters;
   procedure Finish is begin Check (C_Send (Interfaces.C.To_C ('F'), System.Null_Address, 0)); end Finish;
   procedure Declare_Recording (Name, Root, Catalogue, Description : String) is
   begin
      Check (C_Declare_Recording (Interfaces.C.To_C (Name), Interfaces.C.To_C (Root),
        Interfaces.C.To_C (Catalogue), Interfaces.C.To_C (Description)));
   end Declare_Recording;
   procedure Record_Bytes (Name : String; Payload : SDL_Runtime.Bytes) is
   begin
      Check (C_Record (Interfaces.C.To_C (Name), Payload'Address, Interfaces.C.size_t (Payload'Length)));
   end Record_Bytes;
   procedure Declare_Measurement (Name : String) is
   begin
      Check (C_Declare_Measurement (Interfaces.C.To_C (Name)));
   end Declare_Measurement;
   procedure Measure (Name : String; Value : SDL_Runtime.Float64) is
   begin
      Check (C_Measure (Interfaces.C.To_C (Name), Interfaces.C.double (Value)));
   end Measure;
end Ose_Component;
