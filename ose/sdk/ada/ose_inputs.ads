with Ada.Strings.Unbounded;
with Interfaces;
with Ose_Component;
with SDL_Runtime;
package Ose_Inputs is
   type Optional_Id is record
      Present : Boolean := False;
      Value : Interfaces.Unsigned_64 := 0;
   end record;
   No_Id : constant Optional_Id := (False, 0);
   type Selector is record
      App_Id : Optional_Id := No_Id;
      Handle, Source : Ada.Strings.Unbounded.Unbounded_String;
   end record;
   Any_Message : constant Selector := (others => <>);
   type Event_Kind is (Message_Event, Timeout_Event);
   type Metadata is record
      Port, Handle, Source : Ada.Strings.Unbounded.Unbounded_String;
      Kind : Event_Kind := Timeout_Event;
      App_Id : Optional_Id := No_Id;
      Elapsed : Interfaces.Unsigned_64 := 1;
      Due_Ns : Interfaces.Unsigned_64 := 0;
   end record;
   -- Wire records own all text and envelope data; they never borrow a frame.
   type Message_Record is record
      Present : Boolean := False;
      Handle, Source, Catalogue : Ada.Strings.Unbounded.Unbounded_String;
      App_Id : Optional_Id := No_Id;
      Received_Ns : Interfaces.Unsigned_64 := 0;
      Envelope : SDL_Runtime.Buffer;
   end record;
   type Activation is record
      Time_Ns, Index : Interfaces.Unsigned_64 := 0;
      Meta : Metadata;
      Item : Message_Record;
   end record;
   function Parse_Activation (E : Ose_Component.Event) return Activation;
   function Record_Metadata (Port : String; Item : Message_Record) return Metadata;
   procedure Ready;
   procedure Publish (Port : String; Payload : SDL_Runtime.Bytes; Id : Optional_Id := No_Id);
   function Get (Port : String; Selection : Selector := Any_Message; Consume : Boolean := False) return Message_Record;
   function Remove (Port : String; Selection : Selector := Any_Message) return Boolean;
   -- Delay is positive nanoseconds; absence uses the model delay.
   procedure Arm (Port : String; Delay_Ns : Optional_Id := No_Id);
   procedure Disarm (Port : String);
end Ose_Inputs;
