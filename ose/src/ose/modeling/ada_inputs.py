"""Ada CH/2 references and lifecycle, sharing generated context services."""
from ose.modeling.ada_containers import literal


def declarations(node, typ):
    lines = []
    for port, desc in node['inputs'].items():
        if desc.get('kind') == 'timer':
            lines += [f'   subtype Input_{port} is Ose_Inputs.Metadata;']
        else:
            lines += [f'   type Input_{port} is record', '      Meta : Ose_Inputs.Metadata;',
                      '      Present : Boolean := False;', f'      Message : {typ(desc)};', '   end record;']
    lines += ['   type Handler is tagged limited null record;',
              '   procedure On_Start (Self : in out Handler; Ctx : in out Context);',
              '   procedure On_Stop (Self : in out Handler; Ctx : in out Context);']
    for port, desc in node['inputs'].items():
        if desc.get('mode', 'push') != 'pull':
            lines += [f'   procedure On_{port} (Self : in out Handler; Ctx : in out Context; Input : Input_{port});']
        else:
            for operation in ('Get', 'Pull'):
                lines += [f'   function {operation}_{port} (Ctx : in out Context; Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message) return Input_{port};']
            lines += [f'   function Remove_{port} (Ctx : in out Context; Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message) return Boolean;']
    lines += ['   function Activation_Index (Ctx : Context) return Interfaces.Unsigned_64;',
              '   procedure Arm (Ctx : in out Context; Port : String);',
              '   procedure Arm (Ctx : in out Context; Port : String; Delay_Ns : Interfaces.Unsigned_64);',
              '   procedure Disarm (Ctx : in out Context; Port : String);']
    return lines


def context_fields(node):
    lines = ['      Initializing : Boolean := False;']
    for port in node['outputs']:
        lines += [f'      Allowed_{port} : Boolean := False;']
    for port, desc in node['inputs'].items():
        if desc.get('kind') != 'timer':
            lines += [f'      Remote_{port} : Ada.Strings.Unbounded.Unbounded_String;',
                      f'      Decoder_{port} : SDL_Runtime.Context;']
    return lines


def callbacks(node):
    lines = []
    for port, desc in node['inputs'].items():
        if desc.get('mode', 'push') != 'pull':
            lines += [f'   procedure On_{port} (Self : in out Handler; Ctx : in out Context; Input : Input_{port}) is',
                      f'   begin raise Program_Error with "missing On_{port}"; end;']
    return lines


def publications(node, types, typ):
    lines = []
    for port, desc in node['outputs'].items():
        package = types[desc['root']][0]
        signature = f'   procedure Publish_{port} (Ctx : in out Context; Message : {typ(desc)}; Id : Ose_Inputs.Optional_Id := Ose_Inputs.No_Id) is'
        lines += [signature, '   begin',
                  f'      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task or else not Ctx.Allowed_{port} then',
                  '         raise Program_Error with "input event cannot activate output";', '      end if;',
                  f'      Ose_Inputs.Publish ({literal(port)}, {package}.Encode (Ctx.Output_{port}, Message), Id);', '   end;',
                  f'   procedure Publish_{port} (Ctx : in out Context; Message : {typ(desc)}; Id : Interfaces.Unsigned_64) is',
                  f'   begin Publish_{port} (Ctx, Message, Ose_Inputs.Optional_Id\'(True, Id)); end;']
    return lines


def context_methods(node, types, typ):
    lines = ['   function Activation_Index (Ctx : Context) return Interfaces.Unsigned_64 is (Ctx.Index);',
             '   procedure Check_Timer (Ctx : Context; Port : String) is', '   begin',
             '      if (not Ctx.Active and not Ctx.Initializing) or else Ctx.Owner /= Ada.Task_Identification.Current_Task then',
             '         raise Program_Error with "timer control requires initialization or an active callback";', '      end if;']
    timers = [literal(port) for port, desc in node['inputs'].items() if desc.get('kind') == 'timer']
    if timers:
        lines += ['      if not (' + ' or else '.join('Port = ' + port for port in timers) + ') then',
                  '         raise Protocol_Error with "timer control requires a declared timer";', '      end if;']
    else:
        lines += ['      raise Protocol_Error with "component has no timer inputs";']
    lines += ['   end Check_Timer;',
              '   procedure Arm (Ctx : in out Context; Port : String) is',
              '   begin Check_Timer (Ctx, Port); Ose_Inputs.Arm (Port); end;',
              '   procedure Arm (Ctx : in out Context; Port : String; Delay_Ns : Interfaces.Unsigned_64) is',
              '   begin Check_Timer (Ctx, Port); Ose_Inputs.Arm (Port, (True, Delay_Ns)); end;',
              '   procedure Disarm (Ctx : in out Context; Port : String) is',
              '   begin Check_Timer (Ctx, Port); Ose_Inputs.Disarm (Port); end;']
    for port, desc in node['inputs'].items():
        if desc.get('kind') == 'timer':
            continue
        package = types[desc['root']][0]
        lines += [f'   function Decode_{port} (Ctx : in out Context; Item : Ose_Inputs.Message_Record; Meta : Ose_Inputs.Metadata) return Input_{port} is',
                  f'      Result : Input_{port};', '   begin', '      Result.Meta := Meta;',
                  '      if not Item.Present then return Result; end if;',
                  '      if Item.Received_Ns > Ctx.Time then raise Protocol_Error with "input received in the future"; end if;',
                  '      declare',
                  '         Packet : constant SDL_Runtime.Envelope := SDL_Runtime.Unpack_Envelope (SDL_Runtime.To_Bytes (Item.Envelope));',
                  '      begin',
                  '         if Packet.Catalogue /= SDL_Runtime.Catalogue_Id (To_String (Item.Catalogue)) then',
                  '            raise Protocol_Error with "input catalogue identity mismatch";', '         end if;',
                  f'         if Item.Catalogue /= Ctx.Remote_{port} then',
                  f'            if not SDL_Runtime.Is_Compatible (SDL_Runtime.Compare_Catalogues (To_String (Item.Catalogue), {literal(desc["catalogue"])}, {literal(desc["root"])}, True)) then',
                  '               raise Protocol_Error with "incompatible input catalogue";', '            end if;',
                  f'            Ctx.Decoder_{port} := {package}.Prepare (To_String (Item.Catalogue)); Ctx.Remote_{port} := Item.Catalogue;',
                  '         end if;',
                  f'         Result.Message := {package}.Decode (Ctx.Decoder_{port}, Packet.Payload); Result.Present := True;',
                  '      end;', '      return Result;', '   end;']
        if desc.get('mode') == 'pull':
            for operation in ('Get', 'Pull'):
                lines += [f'   function {operation}_{port} (Ctx : in out Context; Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message) return Input_{port} is',
                          '   begin',
                          '      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task then',
                          '         raise Program_Error with "Pull access requires the active callback";', '      end if;',
                          f'      declare Item : constant Ose_Inputs.Message_Record := Ose_Inputs.Get ({literal(port)}, Selection, {str(operation == "Pull")});',
                          f'      begin return Decode_{port} (Ctx, Item, Ose_Inputs.Record_Metadata ({literal(port)}, Item)); end;',
                          '   end;']
            lines += [f'   function Remove_{port} (Ctx : in out Context; Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message) return Boolean is',
                      '   begin',
                      '      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task then',
                      '         raise Program_Error with "Pull access requires the active callback";', '      end if;',
                      f'      return Ose_Inputs.Remove ({literal(port)}, Selection);', '   end;']
    return lines


def parameter_update(node, types):
    if not node.get('parameter_bindings'):
        return []
    lines = ['      procedure Parameter_Update (Event_Value : Event; Initial : Boolean := False) is',
             '         Port, Cat : Unbounded_String;', '         Data : SDL_Runtime.Buffer;', '      begin',
             '         if Op (Event_Value) /= \'U\' then raise Protocol_Error with "expected parameter update"; end if;',
             '         Input (Event_Value, Port, Cat, Data);']
    readable = [(alias, desc) for alias, desc in node['parameter_bindings'].items() if 'read' in desc['access']]
    for index, (alias, desc) in enumerate(readable):
        lines += [f'         {"if" if index == 0 else "elsif"} To_String (Port) = {literal(alias)} then',
                  f'            if To_String (Cat) /= {literal(desc["catalogue"])} or else (Initial and Ctx.Parameter_Present_{alias}) then',
                  '               raise Protocol_Error with "invalid parameter catalogue or duplicate initial value";', '            end if;',
                  f'            Ctx.Parameter_Value_{alias} := {types[desc["root"]][0]}.Decode (Ctx.Parameter_Codec_{alias}, SDL_Runtime.To_Bytes (Data));',
                  f'            Ctx.Parameter_Present_{alias} := True;']
    if readable:
        lines += ['         else']
    lines += ['            raise Protocol_Error with "unknown readable parameter";']
    if readable:
        lines += ['         end if;']
    return lines + ['      end Parameter_Update;']


def runtime(node, types, api):
    lines = ['   procedure Run (Self : in out Handler\'Class) is', '      Ctx : Context;', '      E : Event;',
             '      Started : Boolean := False;'] + parameter_update(node, types)
    lines += ['      procedure Clear_Activation is', '      begin',
              '         Ctx.Active := False; Ctx.Initializing := False; Ctx.Owner := Ada.Task_Identification.Null_Task_Id;']
    if node.get('parameter_bindings'):
        lines += ['         Ctx.Parameter_Writes := False;']
    for port in node['outputs']:
        lines += [f'         Ctx.Allowed_{port} := False;']
    lines += ['      end Clear_Activation;', '   begin', '      Read_Event (E);',
              '      if Op (E) /= \'I\' then raise Protocol_Error with "expected INIT"; end if;',
              '      Ctx.Config := To_Unbounded_String (Body_Text (E));']
    for port, desc in node['outputs'].items():
        lines += [f'      Ctx.Output_{port} := {types[desc["root"]][0]}.Prepare ({literal(desc["catalogue"])});']
    for alias, desc in node.get('parameter_bindings', {}).items():
        lines += [f'      Ctx.Parameter_Codec_{alias} := {types[desc["root"]][0]}.Prepare ({literal(desc["catalogue"])});']
    for desc in node.get('parameter_bindings', {}).values():
        if 'read' in desc['access']:
            lines += ['      Read_Event (E); Parameter_Update (E, True);']
    if node.get('parameter_bindings'):
        lines += ['      Ctx.Parameter_Writes := True;']
    lines += ['      Ctx.Initializing := True; Ctx.Owner := Ada.Task_Identification.Current_Task;',
              '      Started := True; On_Start (Self, Ctx);',
              '      Ctx.Recordings.Frozen := True; Ctx.Measurements.Frozen := True;',
              '      Clear_Activation; Ose_Inputs.Ready;', '      loop', '         Read_Event (E);', '         case Op (E) is',
              "         when 'X' =>", '            if Body_Size (E) /= 0 then raise Protocol_Error with "invalid STOP"; end if;', '            exit;',
              *(["         when 'U' => Parameter_Update (E);"] if node.get('parameter_bindings') else []),
              "         when 'E' => Ose_Recording.Update (Ctx.Recordings, Body_Text (E));",
              "         when 'T' =>", '            declare',
              '               Ref : constant Ose_Inputs.Activation := Ose_Inputs.Parse_Activation (E);',
              '            begin',
              '               if Ctx.Index = Interfaces.Unsigned_64\'Last or else Ref.Index /= Ctx.Index + 1 or else Ref.Time_Ns < Ctx.Time then',
              '                  raise Protocol_Error with "non-monotonic CH/2 activation";', '               end if;',
              '               Ctx.Time := Ref.Time_Ns; Ctx.Index := Ref.Index;',
              '               Ctx.Active := True; Ctx.Owner := Ada.Task_Identification.Current_Task; Ctx.Recordings.Tick_Bytes := 0;']
    if node.get('parameter_bindings'):
        lines += ['               Ctx.Parameter_Writes := True;']
    activating = {port: desc for port, desc in node['inputs'].items() if desc.get('mode', 'push') != 'pull'}
    for index, (port, desc) in enumerate(activating.items()):
        lines += [f'               {"if" if index == 0 else "elsif"} To_String (Ref.Meta.Port) = {literal(port)} then']
        if desc.get('kind') == 'timer':
            check = 'Ref.Meta.Kind /= Ose_Inputs.Timeout_Event'
            if desc['timer']['mode'] == 'one_shot':
                check += ' or else Ref.Meta.Elapsed /= 1'
            lines += [f'                  if {check} then raise Protocol_Error with "invalid timer activation"; end if;']
        elif 'timeout' not in desc:
            lines += ['                  if Ref.Meta.Kind /= Ose_Inputs.Message_Event then raise Protocol_Error with "input has no timeout"; end if;']
        for output in node['outputs']:
            events = [f'Ref.Meta.Kind = Ose_Inputs.{"Message" if event == "message" else "Timeout"}_Event'
                      for event in ('message', 'timeout') if output in desc.get('activates', {}).get(event, [])]
            if events:
                lines += [f'                  Ctx.Allowed_{output} := ' + ' or else '.join(events) + ';']
        ref = 'Ref.Meta' if desc.get('kind') == 'timer' else f'Decode_{port} (Ctx, Ref.Item, Ref.Meta)'
        lines += [f'                  On_{port} (Self, Ctx, {ref});']
    if activating:
        lines += ['               else']
    lines += ['                  raise Protocol_Error with "unknown activating input";']
    if activating:
        lines += ['               end if;']
    lines += ['            end;', '            Clear_Activation; Finish;',
              '         when others => raise Protocol_Error with "unexpected CH/2 opcode";', '         end case;', '      end loop;',
              '      Clear_Activation; Started := False; On_Stop (Self, Ctx);', '   exception when others =>',
              '      Clear_Activation; Ctx.Recordings.Frozen := True; Ctx.Measurements.Frozen := True;',
              '      if Started then begin On_Stop (Self, Ctx); exception when others => null; end; end if;', '      raise;',
              '   end Run;', f'end {api};', '']
    return lines
