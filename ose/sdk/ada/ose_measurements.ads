with Ada.Containers.Indefinite_Hashed_Sets;
with Ada.Strings.Hash;
with SDL_Runtime;
package Ose_Measurements is
   package Name_Sets is new Ada.Containers.Indefinite_Hashed_Sets
     (String, Ada.Strings.Hash, "=");
   type Registry is limited record
      Names : Name_Sets.Set;
      Frozen : Boolean := False;
   end record;
   procedure Declare_Measurement (R : in out Registry; Name : String);
   procedure Measure (R : Registry; Name : String; Value : SDL_Runtime.Float64);
end Ose_Measurements;
