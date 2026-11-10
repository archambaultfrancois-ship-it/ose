with Interfaces;
with SDL_Value;
package body B_Handler is
   use type Interfaces.Unsigned_32;
   overriding procedure On_input (Self : in out Object; Ctx : in out Ose_b.Context; Input : Ose_b.Input_input) is
      pragma Unreferenced (Self);
      Value : SDL_Value.T_Value;
   begin
      if not Input.Present then raise Program_Error with "missing input message"; end if;
      Value.F_count := Input.Message.F_count + 100;
      Ose_b.Publish_out (Ctx, Value, Input.Meta.App_Id);
   end On_input;
end B_Handler;
