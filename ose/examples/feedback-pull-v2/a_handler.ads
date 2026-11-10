with Ose_a;
package A_Handler is
   type Object is new Ose_a.Handler with null record;
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_a.Context);
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_a.Context; Input : Ose_a.Input_clock);
end A_Handler;
