with Interfaces;
with SDL_Runtime;
with SDL_Value;
package body A_Handler is
   use type Interfaces.Unsigned_64;
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_a.Context) is
      pragma Unreferenced (Self);
   begin
      Ose_a.Declare_Measurement (Ctx, "feedback.count");
   end On_Start;
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_a.Context; Input : Ose_a.Input_clock) is
      pragma Unreferenced (Self, Input);
      Previous : constant Ose_a.Input_feedback := Ose_a.Pull_feedback (Ctx);
      Value : SDL_Value.T_Value;
   begin
      if Previous.Present then
         Ose_a.Measure (Ctx, "feedback.count", SDL_Runtime.Float64 (Previous.Message.F_count));
      end if;
      Value.F_count := Interfaces.Unsigned_32 (Ose_a.Activation_Index (Ctx));
      Ose_a.Publish_out (Ctx, Value, Ose_a.Activation_Index (Ctx));
      if Ose_a.Activation_Index (Ctx) = 3 then Ose_a.Disarm (Ctx, "clock"); end if;
   end On_clock;
end A_Handler;
