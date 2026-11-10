"""Ada v2 handler sources shared by generation checks and GNAT execution tests."""
A_SPEC = '''with Ose_COMPONENT_NAME;
package UNIT_NAME is
   type Object is new Ose_COMPONENT_NAME.Handler with null record;
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context);
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_clock);
end UNIT_NAME;
'''
A = '''with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Ada.Strings.Fixed;
with Interfaces;
with Ose_Inputs;
with Ose_Component;
with SDL_Value;
with SDL_Runtime;
package body UNIT_NAME is
   use type Interfaces.Unsigned_32;
   use type Interfaces.Unsigned_64;
   use type Ose_Inputs.Event_Kind;
   function Enabled (Ctx : Ose_COMPONENT_NAME.Context; Key : String) return Boolean is
     (Ada.Strings.Fixed.Index (Ose_COMPONENT_NAME.Config_JSON (Ctx), '"' & Key & """: true") > 0);
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context) is
      pragma Unreferenced (Self);
   begin
      Ose_COMPONENT_NAME.Declare_Measurement (Ctx, "feedback.count");
      if Enabled (Ctx, "arm") then Ose_COMPONENT_NAME.Arm (Ctx, "clock"); end if;
   end On_Start;
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_clock) is
      pragma Unreferenced (Self);
      Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message;
      Previous, Again, Withdrawn : Ose_COMPONENT_NAME.Input_feedback;
      Value : SDL_Value.T_Value;
      Removed : Boolean;
   begin
      pragma Assert (Input.Kind = Ose_Inputs.Timeout_Event);
      if Ada.Strings.Fixed.Index (Ose_COMPONENT_NAME.Config_JSON (Ctx), """delay"": 0.01") > 0 then delay 0.01;
      elsif Ada.Strings.Fixed.Index (Ose_COMPONENT_NAME.Config_JSON (Ctx), """delay"": 0.005") > 0 then delay 0.005; end if;
      if Enabled (Ctx, "bypass") then
         Ose_Component.Send_Event ('P', (0, 3, Character'Pos ('o'), Character'Pos ('u'), Character'Pos ('t'), 0, 1));
         return;
      end if;
      Selection.App_Id := (True, Interfaces.Unsigned_64'Last);
      Previous := Ose_COMPONENT_NAME.Get_feedback (Ctx, Selection);
      if Previous.Present then
         pragma Assert (Previous.Meta.App_Id.Present and then Previous.Meta.App_Id.Value = Interfaces.Unsigned_64'Last);
         Selection.Handle := Previous.Meta.Handle; Selection.Source := Previous.Meta.Source;
         Again := Ose_COMPONENT_NAME.Get_feedback (Ctx, Selection);
         pragma Assert (Again.Present and then Again.Message.F_count = Previous.Message.F_count);
         if Ose_COMPONENT_NAME.Activation_Index (Ctx) = 3 then
            Removed := Ose_COMPONENT_NAME.Remove_feedback (Ctx, Selection); pragma Assert (Removed);
         else
            Withdrawn := Ose_COMPONENT_NAME.Pull_feedback (Ctx, Selection);
            pragma Assert (Withdrawn.Present and then Withdrawn.Meta.Handle = Previous.Meta.Handle);
         end if;
         Again := Ose_COMPONENT_NAME.Get_feedback (Ctx, Selection); pragma Assert (not Again.Present);
         Ose_COMPONENT_NAME.Measure (Ctx, "feedback.count", SDL_Runtime.Float64 (Previous.Message.F_count));
         if Enabled (Ctx, "fail") then
            Value.F_count := 999; Ose_COMPONENT_NAME.Publish_out (Ctx, Value);
            raise Program_Error with "failed after deferred pull and staged publication";
         end if;
      end if;
      Value.F_count := Interfaces.Unsigned_32 (Ose_COMPONENT_NAME.Activation_Index (Ctx));
      Ose_COMPONENT_NAME.Publish_out (Ctx, Value, Interfaces.Unsigned_64'Last);
      if Ose_COMPONENT_NAME.Activation_Index (Ctx) = 3 then Ose_COMPONENT_NAME.Disarm (Ctx, "clock"); end if;
   end On_clock;
end UNIT_NAME;
'''
B_SPEC = '''with Ose_COMPONENT_NAME;
package UNIT_NAME is
   type Object is new Ose_COMPONENT_NAME.Handler with null record;
   overriding procedure On_input (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_input);
end UNIT_NAME;
'''
B = '''with Interfaces;
with Ose_Inputs;
with SDL_Value;
package body UNIT_NAME is
   use type Interfaces.Unsigned_32;
   use type Interfaces.Unsigned_64;
   use type Ose_Inputs.Event_Kind;
   overriding procedure On_input (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_input) is
      pragma Unreferenced (Self);
      Value : SDL_Value.T_Value;
   begin
      if Input.Meta.Kind = Ose_Inputs.Timeout_Event then pragma Assert (not Input.Present); return; end if;
      pragma Assert (Input.Present and then Input.Meta.App_Id.Present and then Input.Meta.App_Id.Value = Interfaces.Unsigned_64'Last);
      Value.F_count := Input.Message.F_count + 100;
      Ose_COMPONENT_NAME.Publish_out (Ctx, Value, Input.Meta.App_Id);
   end On_input;
end UNIT_NAME;
'''
