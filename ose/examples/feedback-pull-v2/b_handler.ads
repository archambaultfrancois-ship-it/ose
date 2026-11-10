with Ose_b;
package B_Handler is
   type Object is new Ose_b.Handler with null record;
   overriding procedure On_input (Self : in out Object; Ctx : in out Ose_b.Context; Input : Ose_b.Input_input);
end B_Handler;
