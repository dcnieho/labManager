from enum import Enum, auto
import asyncio
import concurrent
import json
import time
import math
from dataclasses import dataclass, field

from imgui_bundle import hello_imgui, icons_fontawesome, imgui, immapp, imspinner, imgui_md, imgui_color_text_edit, glfw_utils
from imgui_bundle import portable_file_dialogs
from imgui_bundle.demos_python import demo_utils
import glfw

from labManager.common import async_thread, config, eye_tracker, structs, task
from labManager.master import Master
from ._impl import computer_list, msgbox, utils

# Struct that holds the application's state
class ActionState(Enum):
    Not_Done    = auto()
    Processing  = auto()
    Done        = auto()

@dataclass
class TaskDef:
    type        : task.Type = task.Type.Shell_command   # good default
    payload_type: str       = 'text'
    payload_text: str       = ''
    payload_file: str       = ''
    cwd         : str       = ''
    env         : dict      = field(default_factory=dict)
    interactive : bool      = False

class MainGUI:
    def __init__(self):
        # Constants
        self.popup_stack = []

        self.master = Master()
        self.master.load_known_clients(config.master['clients'])

        self.username         = ''
        self.password         = ''
        self.login_state      = ActionState.Not_Done
        self.proj_select_state= ActionState.Not_Done
        self.proj_idx         = -1
        self.project          = ''  # NB: display name

        # GUI state
        self._window_list     = []
        self._to_dock         = []
        self._main_dock_node_id = None

        self.selected_computers: dict[int, bool] = {k:False for k in self.master.known_clients}
        self.computer_lister  = computer_list.ComputerList(self.master.known_clients, self.selected_computers, info_callback=self._open_computer_detail)

        # task GUI
        self._task_prep: TaskDef = TaskDef()
        self._task_GUI_editor    = imgui_color_text_edit.TextEditor()   # NB: also used for the payload display on a computer pane
        self._task_GUI_editor.set_language_definition(self._task_GUI_editor.LanguageDefinition.python())  # there is no batch, have to live with this...
        self._task_GUI_editor_copy_t = None
        self._task_GUI_open_file_diag = None

        # image management GUI
        self._images_list = []
        self._selected_image_id = None
        self._image_description_cache = {}
        self._active_imaging_tasks = []
        self._active_imaging_tasks_updater = None
        self._active_imaging_tasks_updater_should_stop = False

        # computer detail GUIs
        self._computer_GUI_tasks: dict[int,tuple[str,int]|None] = {}
        self._computer_GUI_interactive_tasks: dict[tuple[int,int],str] = {}
        self._computer_GUI_interactive_sent_finish: dict[tuple[int,int],bool] = {}

        # Show errors in threads
        def asyncexcepthook(future: asyncio.Future):
            try:
                exc = future.exception()
            except concurrent.futures.CancelledError:
                return
            if not exc or type(exc) is msgbox.Exc:
                return
            tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
            if isinstance(exc, asyncio.TimeoutError):
                utils.push_popup(self, msgbox.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", msgbox.MsgBox.warn, more=tb)
                return
            utils.push_popup(self, msgbox.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", msgbox.MsgBox.error)
        async_thread.done_callback = asyncexcepthook


    def _fix_popup_transparency(self):
        frame_bg_col = list(imgui.get_style().color_(imgui.Col_.title_bg_active))
        imgui.get_style().set_color_(imgui.Col_.title_bg_active,(*frame_bg_col[0:3], 1.))
        popup_bg_col = list(imgui.get_style().color_(imgui.Col_.popup_bg))
        imgui.get_style().set_color_(imgui.Col_.popup_bg,(*popup_bg_col[0:3], 1.))

    def _load_fonts(self):
        # It will load them from the assets/ folder.
        assets_folder = demo_utils.demos_assets_folder()
        hello_imgui.set_assets_folder(assets_folder)

        # First, we load the default fonts (the font that was loaded first is the default font)
        hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()

        font_filename = demo_utils.demos_assets_folder() + "/fonts/fontawesome-webfont.ttf"
        size_69 = 69.0 * hello_imgui.dpi_font_loading_factor()
        icons_range = [icons_fontawesome.ICON_MIN_FA, icons_fontawesome.ICON_MAX_FA, 0]

        self.icon_font = msgbox.icon_font = \
            imgui.get_io().fonts.add_font_from_file_ttf(font_filename, size_69, glyph_ranges_as_int_list=icons_range)

    def run(self):
        # help hello_imgui find its assets
        hello_imgui.set_assets_folder(demo_utils.demos_assets_folder())

        # Hello ImGui params (they hold the settings as well as the Gui callbacks)
        runner_params = hello_imgui.RunnerParams()

        # Note: by setting the window title, we also set the name of the ini files into which the settings for the user
        # layout will be stored: Docking_demo.ini (imgui settings) and Docking_demo_appWindow.ini (app window size and position)
        runner_params.app_window_params.window_title = self._get_window_title()

        runner_params.imgui_window_params.menu_app_title = "File"
        runner_params.app_window_params.window_geometry.size = (1400, 700)
        runner_params.app_window_params.restore_previous_geometry = True
        runner_params.callbacks.load_additional_fonts = self._load_fonts
        runner_params.callbacks.pre_new_frame = self._update_windows
        runner_params.callbacks.before_exit = self._logout

        # Status bar, idle throttling
        runner_params.imgui_window_params.show_status_bar = False
        runner_params.imgui_window_params.show_status_fps = False
        runner_params.fps_idling.enable_idling = False

        # Menu bar
        runner_params.imgui_window_params.show_menu_bar = True
        runner_params.callbacks.show_app_menu_items = self._show_app_menu_items


        # optional native events handling
        # runner_params.callbacks.any_backend_event_callback = ...

        ################################################################################################
        # Part 2: Define the application layout and windows
        ################################################################################################

        #    2.1 Define the docking splits,
        #    i.e. the way the screen space is split in different target zones for the dockable windows
        #     We want to split "MainDockSpace" (which is provided automatically) into two zones, like this:
        #
        #    ___________________________________________
        #    |        |                                |
        #    |        |                                |
        #    | Left   |                                |
        #    | Space  |    MainDockSpace               |
        #    |        |                                |
        #    |        |                                |
        #    -------------------------------------------
        #

        # First, tell HelloImGui that we want full screen dock space (this will create "MainDockSpace")
        runner_params.imgui_window_params.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
        )
        runner_params.imgui_window_params.enable_viewports = True

        # Always start with this layout, do not persist changes made by the user
        runner_params.docking_params.layout_condition = hello_imgui.DockingLayoutCondition.application_start

        # This will split the preexisting default dockspace "MainDockSpace" in two parts.
        # Then, add a space to the left which occupies a column whose width is 25% of the app width
        split_main_left = hello_imgui.DockingSplit()
        split_main_left.initial_dock = "MainDockSpace"
        split_main_left.new_dock = "LeftSpace"
        split_main_left.direction = imgui.Dir_.left
        split_main_left.ratio = 0.25
        split_main_left.node_flags = imgui.internal.DockNodeFlagsPrivate_.no_docking | imgui.internal.DockNodeFlagsPrivate_.no_tab_bar

        # Finally, transmit these splits to HelloImGui
        runner_params.docking_params.docking_splits = [split_main_left]

        # 2.2 Define our dockable windows : each window provide a Gui callback, and will be displayed
        #     in a docking split.

        # Computer list on the left
        self.computer_list = hello_imgui.DockableWindow()
        self.computer_list.label = "Computers"
        self.computer_list.dock_space_name = "LeftSpace"
        self.computer_list.gui_function = self._computer_pane
        self.computer_list.can_be_closed = False

        # Finally, transmit these windows to HelloImGui
        runner_params.docking_params.dockable_windows = [
            self.computer_list,
            self._make_main_space_window("Login", self._login_GUI),
        ]

        ################################################################################################
        # Part 3: Run the app
        ################################################################################################
        addons_params = immapp.AddOnsParams()
        addons_params.with_markdown = True
        immapp.run(runner_params, addons_params)

    def _update_windows(self):
        # update windows to be shown
        if self._window_list:
            hello_imgui.get_runner_params().docking_params.dockable_windows = self._window_list
            self._window_list = []
        else:
            # check if any computer detail windows were closed. Those should be removed from the list
            hello_imgui.get_runner_params().docking_params.dockable_windows = \
                [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if not w.label.endswith('computer_view') or w.is_visible]

        # we also handle docking requests here
        if self._to_dock:
            for w in self._to_dock:
                imgui.internal.dock_builder_dock_window(w, self._main_dock_node_id)
            self._to_dock = []

    def _make_main_space_window(self, name, gui_func, can_be_closed=False):
        main_space_view = hello_imgui.DockableWindow()
        main_space_view.label = name
        main_space_view.dock_space_name = "MainDockSpace"
        main_space_view.gui_function = gui_func
        main_space_view.can_be_closed = can_be_closed
        return main_space_view

    def _show_app_menu_items(self):
        do_close , _ = imgui.menu_item("Close project", "", False)
        do_logout, _ = imgui.menu_item("Log out", "", False)

        if do_logout:
            self._logout()
        elif do_close:
            self._unload_project()

    def _get_window_title(self, add_user=False, add_project=False):
        title = "labManager Master"
        if add_user and add_project:
            title+= f' ({self.username}/{self.project})'
        elif add_user:
            title+= f' ({self.username})'
        return title

    def _set_window_title(self, add_user=False, add_project=False):
        new_title = self._get_window_title(add_user,add_project)
        # this is just for show, doesn't trigger an update. But lets keep them in sync
        hello_imgui.get_runner_params().app_window_params.window_title = new_title
        # actually update window title.
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_window_title(win, new_title)

    def _login_done(self):
        self.login_state = ActionState.Done
        self._set_window_title(add_user=True)

    def _logout(self):
        self._unload_project()

        self.username         = ''
        self.password         = ''
        self.login_state      = ActionState.Not_Done
        self.master.logout()
        self._set_window_title()

    def _project_selected(self):
        self.proj_select_state = ActionState.Done
        # update GUI
        project = list(self.master.projects.keys())[self.proj_idx]
        self.project = self.master.projects[project]
        if self.project!=project:
            self.project = f'{project} ({self.project})'
        self._window_list = [
            self.computer_list,
            self._make_main_space_window("Tasks", self._task_GUI),
            self._make_main_space_window("Image Management", self._imaging_GUI),
            ]
        self._to_dock = ["Tasks", "Image Management"]
        self._set_window_title(add_user=True, add_project=True)
        # prep for image management
        async_thread.run(self._get_project_images())
        # start server
        async_thread.run(self.master.start_server())

    async def _get_project_images(self):
        temp_list = await self.master.get_images()
        # also get image size on disk
        for im in temp_list:
            im['DiskSize'] = await self.master.get_image_size(im['Id'])
        # flag if the images belong to the selected project
        # simple trick: if image belongs to the project, then its user-facing name doesn't
        # match its name (since the project prefix is removed)
        for im in temp_list:
            im['PartOfProject'] = im['UserFacingName'] != im['Name']
        # atomic update so we can't read incomplete state elsewhere
        self._images_list = temp_list


    def _unload_project(self):
        if self.master.is_serving():
            async_thread.wait(self.master.stop_server())
        self._selected_image_id = None
        self._images_list       = []
        self.proj_select_state  = ActionState.Not_Done
        self.proj_idx           = -1
        self.project            = ''
        self.master.unset_project()

        # reset GUI
        self._task_prep = TaskDef()
        self._window_list = [self.computer_list, self._make_main_space_window("Login", self._login_GUI)]
        self._set_window_title(add_user=True)

    def _login_GUI(self):
        if not self._main_dock_node_id:
            # this window is docked to the right dock node, query id of this dock node as we'll need it for later
            # windows
            self._main_dock_node_id = imgui.get_window_dock_id()
        if self.login_state != ActionState.Done:
            disabled = self.login_state==ActionState.Processing
            if disabled:
                utils.push_disabled()
            if 'login' in config.master:
                i1,self.username = imgui.input_text_with_hint('User name',config.master['login']['hint'], self.username, flags=imgui.InputTextFlags_.enter_returns_true)
            else:
                i1,self.username = imgui.input_text          ('User name',                                self.username, flags=imgui.InputTextFlags_.enter_returns_true)
            i2,self.password = imgui.input_text('Password', self.password, flags=imgui.InputTextFlags_.enter_returns_true|imgui.InputTextFlags_.password)

            if self.login_state==ActionState.Processing:
                symbol_size = imgui.calc_text_size("x").y*2
                spinner_radii = [x/22/2*symbol_size for x in [22, 16, 10]]
                lw = 3.5/22/2*symbol_size
                imspinner.spinner_ang_triple(f'loginSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
            else:
                if imgui.button("Log in") | i1 | i2:
                    if not self.username:
                        utils.push_popup(self, msgbox.msgbox, "Login error", 'Fill in a username', msgbox.MsgBox.error)
                    else:
                        self.login_state = ActionState.Processing
                        async_thread.run(self.master.login(self.username,self.password), lambda fut: self._login_projectsel_result('login',fut))

            if disabled:
                utils.pop_disabled()
        else:
            disabled = self.proj_select_state==ActionState.Processing
            if disabled:
                utils.push_disabled()
            imgui.text('Select project:')
            projects = []
            for p,pn in self.master.projects.items():
                if pn==p:
                    projects.append(p)
                else:
                    projects.append(f'{p} ({pn})')
            _,self.proj_idx = imgui.list_box('##Project', 0 if self.proj_idx==-1 else self.proj_idx, projects)

            if self.proj_select_state==ActionState.Processing:
                symbol_size = imgui.calc_text_size("x").y*2
                spinner_radii = [x/22/2*symbol_size for x in [22, 16, 10]]
                lw = 3.5/22/2*symbol_size
                imspinner.spinner_ang_triple(f'projSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
            else:
                if imgui.button("Select"):
                    self.proj_select_state = ActionState.Processing
                    async_thread.run(self.master.set_project(list(self.master.projects.keys())[self.proj_idx]), lambda fut: self._login_projectsel_result('project',fut))

            if disabled:
                utils.pop_disabled()

    def _login_projectsel_result(self, stage, future: asyncio.Future):
        try:
            exc = future.exception()
        except concurrent.futures.CancelledError:
            return
        if not exc:
            # log in successful
            if stage=='login':
                self._login_done()
            elif stage=='project':
                self._project_selected()
            return

        # error occurred
        if stage=='login':
            self.login_state = ActionState.Not_Done
        elif stage=='project':
            self.proj_select_state = ActionState.Not_Done
        msg = str(exc)
        if '401' in msg:
            msg = msg.splitlines()
            try:
                msg = json.loads(msg[-1])['detail']
                utils.push_popup(self, msgbox.msgbox, f"{'Login' if stage=='login' else 'Project selection'} error", msg, msgbox.MsgBox.error)
                return
            except:
                pass
        if '404' in msg and 'User not found' in msg:
            # lost session on server side, update GUI to reflect that
            self._logout()

        # not handled by above, display more generic error
        tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
        utils.push_popup(self, msgbox.msgbox, "Login error", f"Something went wrong when {'logging in' if stage=='login' else 'selecting project'}...", msgbox.MsgBox.error, more=tb)


    def _task_GUI(self):
        dock_space_id = imgui.get_id("TasksDockSpace")
        if not imgui.internal.dock_builder_get_node(dock_space_id):
            # first time this GUI is shown, set up as follows:
            #    ____________________________________________
            #    |        |        |                        |
            #    |        |        |         Action         |
            #    | Action | Action |         Config         |
            #    |  List  |  Type  |                        |
            #    |        |        |------------------------|
            #    |        |        |         Buttons        |
            #    --------------------------------------------
            imgui.internal.dock_builder_remove_node(dock_space_id)
            imgui.internal.dock_builder_add_node(dock_space_id)

            self._imaging_GUI_list_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(dock_space_id, imgui.Dir_.left,0.15)
            self._task_GUI_type_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.left,.15/(1-.15))
            self._imaging_GUI_details_dock_id,_,self._imaging_GUI_action_dock_id = \
                imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.up,0.90)

            imgui.internal.dock_builder_dock_window('task_list_pane',self._imaging_GUI_list_dock_id)
            imgui.internal.dock_builder_dock_window('task_type_pane',self._task_GUI_type_dock_id)
            imgui.internal.dock_builder_dock_window('task_config_pane',self._imaging_GUI_details_dock_id)
            imgui.internal.dock_builder_dock_window('task_confirm_pane',self._imaging_GUI_action_dock_id)
            imgui.internal.dock_builder_finish(dock_space_id)
        imgui.dock_space(dock_space_id, (0.,0.), imgui.DockNodeFlags_.no_split|imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)

        if imgui.begin('task_list_pane'):
            for t in config.master['tasks']:
                if imgui.button(t['name']):
                    self._task_prep.type        = task.Type(t['type'])
                    self._task_prep.payload_type= t['payload_type']
                    if t['payload_type']=='text':
                        self._task_prep.payload_text = t['payload']
                    else:
                        self._task_prep.payload_file = t['payload']
                    self._task_prep.cwd         = t['cwd']
                    self._task_prep.env         = t['env']
                    self._task_prep.interactive = t['interactive']
        imgui.end()
        if imgui.begin('task_type_pane'):
            for t in task.Type:
                if imgui.radio_button(t.value, self._task_prep.type==t):
                    old_type = self._task_prep.type
                    self._task_prep.type = t
                    # remove command if not wanted
                    if t==task.Type.Wake_on_LAN:
                        self._task_prep.payload_file = self._task_prep.payload_text = ''
                    # make sure we don't have a multiline commands in a single-line
                    # textbox
                    if old_type in [task.Type.Batch_file, task.Type.Python_script]:
                        if t not in [task.Type.Batch_file, task.Type.Python_script]:
                            self._task_prep.payload_type = 'text'
                            self._task_prep.payload_text = utils.trim_str(self._task_prep.payload_text)
                utils.draw_hover_text(t.doc, text='')
        imgui.end()
        if imgui.begin('task_config_pane'):
            if self._task_prep.type==task.Type.Wake_on_LAN:
                imgui.text('Wake on LAN action has no parameters')
            else:
                multiline = False
                can_select_payload_type = False
                match self._task_prep.type:
                    case task.Type.Shell_command:
                        field_name = 'Command'
                    case task.Type.Process_exec:
                        field_name = 'Executable and arguments'
                    case task.Type.Batch_file:
                        if self._task_prep.payload_type=='text':
                            field_name = 'Batch file contents'
                            multiline = True
                        else:
                            field_name = 'Batch file'
                        can_select_payload_type = True
                    case task.Type.Python_statement:
                        field_name = 'Statement'
                    case task.Type.Python_module:
                        field_name = 'Module'
                    case task.Type.Python_script:
                        if self._task_prep.payload_type=='text':
                            field_name = 'Python script contents'
                            multiline = True
                        else:
                            field_name = 'Python script'
                        can_select_payload_type = True
                if can_select_payload_type:
                    if imgui.radio_button('text', self._task_prep.payload_type=='text'):
                        self._task_prep.payload_type='text'
                    imgui.same_line()
                    if imgui.radio_button('file', self._task_prep.payload_type=='file'):
                        self._task_prep.payload_type='file'
                if self._task_prep.payload_type=='text':
                    if multiline:
                        # based on immapp.snippets.show_code_snippet
                        width = imgui.get_content_region_max().x - imgui.get_window_content_region_min().x - imgui.get_style().item_spacing.x

                        imgui.push_font(imgui_md.get_code_font())
                        line_height = imgui.get_font_size()
                        imgui.pop_font()
                        num_visible_lines = 25
                        editor_size = imgui.ImVec2(width, line_height*(num_visible_lines+1))

                        imgui.push_id(imgui.get_id('command_editor'))
                        imgui.begin_group()

                        top_left = imgui.get_cursor_pos()
                        top_right= imgui.ImVec2(top_left.x + editor_size.x, top_left.y)
                        text_y = top_right.y + line_height * 0.2
                        imgui.set_cursor_pos((top_left.x, text_y))
                        imgui.text(field_name)

                        text_x = top_right.x - line_height * 6.
                        imgui.set_cursor_pos((text_x, text_y))
                        pos = self._task_GUI_editor.get_cursor_position()
                        imgui.text(f"L:{pos.m_line+1:3d} C:{pos.m_column+1:3d}")

                        imgui.set_cursor_pos((top_right.x - line_height * 1.5, top_right.y))
                        if imgui.button(icons_fontawesome.ICON_FA_COPY):
                            self._task_GUI_editor_copy_t = immapp.clock_seconds()
                            imgui.set_clipboard_text(self._task_GUI_editor.get_text())

                        if imgui.is_item_hovered():
                            was_copied_recently = self._task_GUI_editor_copy_t is not None and (immapp.clock_seconds()-self._task_GUI_editor_copy_t) < 0.7
                            if was_copied_recently:
                                imgui.set_tooltip("Copied!")
                            else:
                                imgui.set_tooltip("Copy")

                        imgui.set_cursor_pos(top_right)
                        imgui.new_line()

                        imgui.push_font(imgui_md.get_code_font())
                        if self._task_GUI_editor.get_text() != self._task_prep.payload_text:
                            self._task_GUI_editor.set_text(self._task_prep.payload_text)
                        if self._task_GUI_editor.is_read_only():
                            self._task_GUI_editor.set_read_only(False)
                        self._task_GUI_editor.render("Code", False, editor_size)
                        self._task_prep.payload_text = self._task_GUI_editor.get_text()
                        imgui.pop_font()
                        imgui.end_group()
                        imgui.pop_id()
                    else:
                        imgui.text(field_name)
                        imgui.push_font(imgui_md.get_code_font())
                        _, self._task_prep.payload_text = imgui.input_text(f'##{field_name}', self._task_prep.payload_text)
                        imgui.pop_font()
                else:
                    imgui.push_font(imgui_md.get_code_font())
                    _, self._task_prep.payload_file = imgui.input_text('##file_inputter',self._task_prep.payload_file)
                    imgui.pop_font()
                    if imgui.button("Select file"):
                        self._task_GUI_open_file_diag = \
                            portable_file_dialogs.open_file(f'Select {"script" if self._task_prep.type==task.Type.Python_script else "batch file"}')
                    if self._task_GUI_open_file_diag is not None and self._task_GUI_open_file_diag.ready():
                        res = self._task_GUI_open_file_diag.result()
                        if res and isinstance(res,list):
                            self._task_prep.payload_file = res[0]
                        self._task_GUI_open_file_diag = None
                    imgui.same_line()
                    if not self._task_prep.payload_file:
                        utils.push_disabled()
                    if imgui.button('Load file'):
                        try:
                            with open(self._task_prep.payload_file, 'rt') as file:
                                self._task_prep.payload_text = file.read()
                                self._task_prep.payload_type = 'text'
                        except Exception as ex:
                            utils.push_popup(self, msgbox.msgbox, "File reading error", f"Opening the file '{self._task_prep.payload_file}' has failed:\n{type(ex).__name__}: {str(ex) or 'No further details'}", msgbox.MsgBox.error)
                    if not self._task_prep.payload_file:
                        utils.pop_disabled()
                imgui.begin_group()
                imgui.text('Working directory')
                imgui.push_font(imgui_md.get_code_font())
                _, self._task_prep.cwd = imgui.input_text('##cwd', self._task_prep.cwd)
                imgui.pop_font()
                imgui.end_group()
                utils.draw_hover_text('Working directory from which the command will be executed',text='')
                _, self._task_prep.interactive = imgui.checkbox('Interactive', self._task_prep.interactive)
                utils.draw_hover_text('If enabled, it is possible to send input (stdin) to the running command',text='')
        imgui.end()
        if imgui.begin('task_confirm_pane'):
            if imgui.button("Run"):
                selected_clients = [id for id in self.selected_computers if self.selected_computers[id]]
                async_thread.run(
                    self.master.run_task(
                        self._task_prep.type,
                        self._task_prep.payload_text if self._task_prep.payload_type=='text' else self._task_prep.payload_file,
                        selected_clients,
                        self._task_prep.payload_type,
                        self._task_prep.cwd,
                        self._task_prep.env,
                        self._task_prep.interactive
                    )
                )
            imgui.same_line(imgui.get_content_region_avail().x-imgui.calc_text_size('Clear').x-2*imgui.get_style().frame_padding.x)
            if imgui.button('Clear'):
                self._task_prep = TaskDef()
        imgui.end()

    def _imaging_GUI(self):
        dock_space_id = imgui.get_id("ImagingDockSpace")
        if not imgui.internal.dock_builder_get_node(dock_space_id):
            # first time this GUI is shown, set up as follows:
            #    ____________________________________
            #    |        |      Image details      |
            #    | Image  |-------------------------|
            #    |  List  |          Image          |
            #    |        |         Actions         |
            #    ------------------------------------
            imgui.internal.dock_builder_remove_node(dock_space_id)
            imgui.internal.dock_builder_add_node(dock_space_id)

            self._imaging_GUI_list_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(dock_space_id, imgui.Dir_.left,0.20)
            self._imaging_GUI_details_dock_id,_,self._imaging_GUI_action_dock_id = imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.up,0.8)

            imgui.internal.dock_builder_dock_window('images_list_pane',self._imaging_GUI_list_dock_id)
            imgui.internal.dock_builder_dock_window('image_details_pane',self._imaging_GUI_details_dock_id)
            imgui.internal.dock_builder_dock_window('imaging_actions_pane',self._imaging_GUI_action_dock_id)
            imgui.internal.dock_builder_finish(dock_space_id)
        imgui.dock_space(dock_space_id, (0.,0.), imgui.DockNodeFlags_.no_split|imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)

        if imgui.begin('images_list_pane'):
            if imgui.button('+'):
                new_image_name = ''
                def _add_image_popup():
                    nonlocal new_image_name
                    imgui.dummy((30*imgui.calc_text_size('x').x,0))
                    if imgui.begin_table("##new_image_info",2):
                        imgui.table_setup_column("##new_image_infos_left", imgui.TableColumnFlags_.width_fixed)
                        imgui.table_setup_column("##new_image_infos_right", imgui.TableColumnFlags_.width_stretch)
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        imgui.text("Image name")
                        imgui.table_next_column()
                        imgui.set_next_item_width(-1)
                        _,new_image_name = imgui.input_text("##new_image_name",new_image_name)
                        imgui.end_table()

                buttons = {
                    icons_fontawesome.ICON_FA_CHECK+" Add image": lambda: async_thread.run(self.master.create_image(new_image_name),
                                                                                           lambda fut: self._image_action_result('create',fut)),
                    icons_fontawesome.ICON_FA_BAN+" Cancel": None
                }
                utils.push_popup(self, lambda: utils.popup("Add image", _add_image_popup, buttons = buttons, closable=True))

            for im in self._images_list:
                if imgui.button(im['UserFacingName']):
                    self._selected_image_id = im['Id']
        imgui.end()
        im = None
        for i in self._images_list:
            if i['Id']==self._selected_image_id:
                im=i
                break
        if imgui.begin('image_details_pane'):
            if self._selected_image_id:
                if not im:
                    self._selected_image_id = None
                    if self._active_imaging_tasks_updater:
                        self._active_imaging_tasks_updater_should_stop = True
                else:
                    if not self._active_imaging_tasks_updater:
                        self._active_imaging_tasks_updater = async_thread.run(self.update_running_image_tasks())
                    if im['Id'] not in self._image_description_cache:
                        self._image_description_cache[im['Id']] = im['Description']
                    if imgui.begin_table("##image_infos",2):
                        imgui.table_setup_column("##image_infos_left", imgui.TableColumnFlags_.width_fixed)
                        imgui.table_setup_column("##image_infos_right", imgui.TableColumnFlags_.width_stretch)
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text("Image name")
                        imgui.table_next_column()
                        imgui.text(im['UserFacingName'])
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text("Description")
                        imgui.table_next_column()
                        if (disabled := not im['PartOfProject']):
                            utils.push_disabled()
                        changed,self._image_description_cache[im['Id']] = imgui.input_text_multiline(f"##image{im['Id']}_description",self._image_description_cache[im['Id']],flags=imgui.InputTextFlags_.allow_tab_input|imgui.InputTextFlags_.enter_returns_true)
                        if disabled:
                            utils.pop_disabled()
                        do_update = False
                        if self._image_description_cache[im['Id']]!=im['Description']:
                            imgui.same_line()
                            imgui.begin_group()
                            do_update = imgui.button('Save')
                            if imgui.button('Discard changes'):
                                self._image_description_cache[im['Id']] = im['Description']
                            imgui.end_group()
                        if changed | do_update:
                            async_thread.run(self.master.update_image(im['Name'],{'Description':self._image_description_cache[im['Id']]}),
                                             lambda fut: self._image_action_result('update',fut))
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text("Size on disk")
                        imgui.table_next_column()
                        if 'DiskSize' in im:
                            imgui.text(im['DiskSize'])
                        imgui.end_table()
                    active_imaging_tasks = self.get_running_imaging_tasks(self._selected_image_id)
                    imgui.text(f"{len(active_imaging_tasks)} running tasks for this image")
                    if active_imaging_tasks:
                        if imgui.begin_table("##image_tasks_list",9):
                            imgui.table_setup_column("Computer name", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("Status", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("Type", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("Partition", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("Elapsed", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("Remaining", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("Completed", imgui.TableColumnFlags_.width_stretch)
                            imgui.table_setup_column("Rate", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_setup_column("##cancel_button", imgui.TableColumnFlags_.width_fixed)
                            imgui.table_next_row(imgui.TableRowFlags_.headers)
                            for i in range(9):
                                imgui.table_set_column_index(i)
                                imgui.table_header(imgui.table_get_column_name(i))
                            for t in active_imaging_tasks:
                                imgui.table_next_row()
                                imgui.table_next_column()
                                imgui.text(t['ComputerName'])
                                imgui.table_next_column()
                                imgui.text(t['Status'])
                                imgui.table_next_column()
                                imgui.text(t['Type'])
                                imgui.table_next_column()
                                imgui.text(t['Partition'])
                                imgui.table_next_column()
                                imgui.text(t['Elapsed'])
                                imgui.table_next_column()
                                imgui.text(t['Remaining'])
                                imgui.table_next_column()
                                if t['Completed']:
                                    imgui.progress_bar(float(t['Completed'].strip('%'))/100)
                                imgui.table_next_column()
                                imgui.text(t['Rate'])
                                imgui.table_next_column()
                                if imgui.button(f'Cancel##{t["ComputerName"]}'):
                                    async_thread.run(self.master.delete_active_imaging_task(t['TaskId']),
                                                     lambda fut: self._image_action_result('cancel active task',fut))
                            imgui.end_table()
                        if imgui.button('Cancel all'):
                            # NB: cannot use the /ActiveImagingTask/CancelAllImagingTasks toems action, thats only for admins
                            # so issue cancels one by one
                            for t in active_imaging_tasks:
                                async_thread.run(self.master.delete_active_imaging_task(t['TaskId']),
                                                 lambda fut: self._image_action_result('cancel active task',fut))

        imgui.end()
        if imgui.begin('imaging_actions_pane'):
            if im:
                selected_clients = [id for id in self.selected_computers if self.selected_computers[id]]
                if (disabled := not selected_clients):
                    utils.push_disabled()
                if imgui.button('Deploy'):
                    async_thread.run(self.master.deploy_image(im['Name'], [self.master.known_clients[i].name for i in selected_clients]),
                                     lambda fut: self._image_action_result('deploy',fut))
                if not disabled and imgui.is_item_hovered():
                    stations_txt = '\n  '.join((self.master.known_clients[i].name for i in selected_clients))
                    utils.draw_tooltip(f"Deploy image '{im['Name']}' to selected stations:\n  "+stations_txt)
                if disabled:
                    utils.pop_disabled()

                if im['PartOfProject']:
                    if (disabled := len(selected_clients)!=1):
                        utils.push_disabled()
                    if imgui.button('Upload'):
                        async_thread.run(self.master.upload_computer_to_image(next((self.master.known_clients[i].name for i in selected_clients)), im['Name']),
                                        lambda fut: self._image_action_result('upload',fut))
                    if not disabled and imgui.is_item_hovered():
                        station_txt = next((self.master.known_clients[i].name for i in selected_clients))
                        utils.draw_tooltip(f"Upload station {station_txt} to image '{im['Name']}'")
                    if disabled:
                        utils.pop_disabled()

                    imgui.push_style_color(imgui.Col_.button, imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.43)))
                    imgui.push_style_color(imgui.Col_.button_hovered, imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.64)))
                    imgui.push_style_color(imgui.Col_.button_active, imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.93)))
                    if imgui.button('Delete'):
                        async_thread.run(self.master.delete_image(im['Name']),
                                        lambda fut: self._image_action_result('delete',fut))
                    imgui.pop_style_color(3)
        imgui.end()

    async def update_running_image_tasks(self):
        self._active_imaging_tasks_updater_should_stop = False
        while self._active_imaging_tasks_updater_should_stop:
            # NB: this is and must remain an atomic update, so its not possible to read incomplete state elsewhere
            self._active_imaging_tasks = await self.master.get_active_imaging_tasks()

            # sleep until the next whole second
            now = time.time()
            await asyncio.sleep(math.ceil(now) - now)
        self._active_imaging_tasks_updater = None
    def get_running_imaging_tasks(self, id: int|None):
        if id is None:
            return self._active_imaging_tasks
        else:
            return [t for t in self._active_imaging_tasks if t['ImageId']==id]

    def _image_action_result(self, action, future: asyncio.Future):
        try:
            exc = future.exception()
        except concurrent.futures.CancelledError:
            return
        if not exc:
            # action successful, refresh image cache if needed
            if action in ['create','update','deploy','update','delete']:
                async_thread.run(self._get_project_images())
            return

        # error occurred
        msg = str(exc)
        if '401' in msg:
            msg = msg.splitlines()
            try:
                msg = json.loads(msg[-1])['detail']
                utils.push_popup(self, msgbox.msgbox, f"Image {action} error: Not authorized", msg, msgbox.MsgBox.error)
                return
            except:
                pass
        if '403' in msg:
            msg = msg.splitlines()
            try:
                msg = json.loads(msg[-1])['detail']
                utils.push_popup(self, msgbox.msgbox, f"Image {action} error: No permission", msg, msgbox.MsgBox.error)
                return
            except:
                pass
        if '409' in msg:
            msg = msg.splitlines()
            try:
                msg = json.loads(msg[-1])['detail']
                utils.push_popup(self, msgbox.msgbox, f"Image {action} error: Already exists", msg, msgbox.MsgBox.error)
                return
            except:
                pass
        if '404' in msg and 'User not found' in msg:
            # lost session on server side, update GUI to reflect that
            self._logout()

        # not handled by above, display more generic error
        tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
        utils.push_popup(self, msgbox.msgbox, f"Image {action}", f"Something went wrong with the image {action} action...", msgbox.MsgBox.error, more=tb)


    def _open_computer_detail(self, item: structs.KnownClient):
        win = next((x for x in hello_imgui.get_runner_params().docking_params.dockable_windows if x.label==item.name), None)
        if win:
            win.focus_window_at_next_frame = True
        else:
            win_name = f'{item.name}##computer_view'
            self._window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
            self._window_list.append(
                self._make_main_space_window(win_name, lambda: self._computer_detail_GUI(item), can_be_closed=True)
            )
            self._to_dock = [win_name]
            self._computer_GUI_tasks[item.id] = None

    def _computer_detail_GUI(self, item: structs.KnownClient):
        if not item.client:
            # clear state about this computer
            self._computer_GUI_tasks[item.id] = None
        dock_space_id = imgui.get_id(f"ComputerDockSpace_{item.id}")
        if not imgui.internal.dock_builder_get_node(dock_space_id):
            # first time this GUI is shown, set up as follows:
            #    ____________________________________
            #    |  Task  |        Task result      |
            #    |   +    |-------------------------|
            #    |  Event |           Log           |
            #    |  List  |          Details        |
            #    ------------------------------------
            imgui.internal.dock_builder_remove_node(dock_space_id)
            imgui.internal.dock_builder_add_node(dock_space_id)

            self._imaging_GUI_list_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(dock_space_id, imgui.Dir_.left,0.15)
            self._imaging_GUI_details_dock_id,_,self._imaging_GUI_action_dock_id = imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.up,0.15)

            imgui.internal.dock_builder_dock_window(f'task_list_pane_{item.id}',self._imaging_GUI_list_dock_id)
            imgui.internal.dock_builder_dock_window(f'task_result_pane_{item.id}',self._imaging_GUI_details_dock_id)
            imgui.internal.dock_builder_dock_window(f'task_log_pane_{item.id}',self._imaging_GUI_action_dock_id)
            imgui.internal.dock_builder_finish(dock_space_id)
        imgui.dock_space(dock_space_id, (0.,0.), imgui.DockNodeFlags_.no_split|imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)

        if imgui.begin(f'task_list_pane_{item.id}'):
            if item.client:
                if item.client.tasks:
                    imgui.text('Tasks:')
                    imgui.push_font(imgui_md.get_code_font())
                    for id in item.client.tasks:
                        tsk = item.client.tasks[id]
                        if tsk.type==task.Type.Wake_on_LAN:
                            lbl = tsk.type.value
                            hover_text = tsk.type.value
                        else:
                            lbl = utils.trim_str(tsk.payload, length=12, newline_ellipsis=True)
                            hover_text = tsk.type.value+':\n'+tsk.payload
                        if imgui.button(f'{lbl}##{tsk.id}'):
                            self._computer_GUI_tasks[item.id] = ('task',id)
                        if imgui.is_item_hovered():
                            # show no more than 10 lines
                            lines = hover_text.splitlines()
                            to_show = '\n'.join(lines[0:10])
                            if len(lines)>10:
                                to_show += '\n...'
                            utils.draw_tooltip(to_show)
                    imgui.pop_font()
                if self.master.client_et_events[item.client.id]:
                    imgui.text('Eye-tracker events:')
                    for i,evt in enumerate(self.master.client_et_events[item.client.id]):
                        str,full_info,_ = eye_tracker.format_event(evt)
                        lbl = utils.trim_str(str, length=12, newline_ellipsis=True)
                        if imgui.button(f'{lbl}##et_{i}'):
                            self._computer_GUI_tasks[item.id] = ('ET',i)
                        utils.draw_hover_text(hover_text=full_info,text='')
                if not item.client.tasks and not self.master.client_et_events[item.client.id]:
                    imgui.text_wrapped('no tasks or eye tracker events available')
        imgui.end()
        if imgui.begin(f'task_result_pane_{item.id}'):
            if item.client and (tid := self._computer_GUI_tasks[item.id]) is not None:
                if tid[0]=='task':
                    tsk = item.client.tasks[tid[1]]
                    if tsk.type==task.Type.Wake_on_LAN:
                        imgui.text(tsk.type.value)
                    else:
                        imgui.text(f'{tsk.type.value}:')
                        imgui.push_font(imgui_md.get_code_font())
                        if tsk.type in [task.Type.Batch_file, task.Type.Python_script]:
                            if self._task_GUI_editor.get_text() != tsk.payload.replace('\r\n','\n').replace('\r','\n'):
                                self._task_GUI_editor.set_text(tsk.payload)
                            if not self._task_GUI_editor.is_read_only():
                                self._task_GUI_editor.set_read_only(True)
                            width = imgui.get_content_region_max().x - imgui.get_window_content_region_min().x - imgui.get_style().item_spacing.x
                            line_height = imgui.get_font_size()
                            num_visible_lines = 10
                            editor_size = imgui.ImVec2(width, line_height*(num_visible_lines+1))
                            self._task_GUI_editor.render("Code", False, editor_size)
                        else:
                            utils.push_disabled()
                            imgui.input_text(f'##task_payload', tsk.payload)
                            utils.pop_disabled()
                        imgui.pop_font()
                    imgui.text(tsk.status.value)
                    if tsk.return_code:
                        imgui.text(f'return code: {tsk.return_code}')
                    if tsk.status in [task.Status.Not_started, task.Status.Running]:
                        imgui.same_line()
                        if tsk.status==task.Status.Not_started:
                            button_txt = 'Cancel'
                        else:
                            if tsk.interactive and (item.id, tid[1]) not in self._computer_GUI_interactive_sent_finish:
                                button_txt = 'Finish'
                            else:
                                button_txt = 'Stop'
                        if imgui.button(f'{button_txt}##{tid[1]}'):
                            async_thread.run(task.send_cancel(item,tsk))
                            if tsk.interactive:
                                self._computer_GUI_interactive_sent_finish[(item.id, tid[1])] = True
                elif tid[0]=='ET':
                    evt = self.master.client_et_events[item.client.id][tid[1]]
                    _,_,evt_info = eye_tracker.format_event(evt)
                    imgui.text(f'Timestamp: {evt_info[0]}')
                    imgui.text(f'Event: {evt_info[1]}')
                    if len(evt_info)>2:
                        imgui.text(f'Info: {evt_info[2]}')
            elif item.client:
                imgui.text('select a task or eye tracker event on the left')
        imgui.end()
        if imgui.begin(f'task_log_pane_{item.id}'):
            if item.client and (tid := self._computer_GUI_tasks[item.id]) is not None:
                if tid[0]=='task' and (tsk:=item.client.tasks[tid[1]]).type!=task.Type.Wake_on_LAN:
                    if tsk.interactive and tsk.status==task.Status.Running and ((item.id, tid[1]) not in self._computer_GUI_interactive_sent_finish or not self._computer_GUI_interactive_sent_finish[(item.id, tid[1])]):
                        if (item.id, tid[1]) not in self._computer_GUI_interactive_tasks:
                            self._computer_GUI_interactive_tasks[(item.id, tid[1])] = ''
                        entered, self._computer_GUI_interactive_tasks[(item.id, tid[1])] = \
                            imgui.input_text(f'##interactive_input{item.id},{tid[1]}', self._computer_GUI_interactive_tasks[(item.id, tid[1])], flags=imgui.InputTextFlags_.enter_returns_true)
                        if (disabled := not self._computer_GUI_interactive_tasks[(item.id, tid[1])]):
                            utils.push_disabled()
                        imgui.same_line()
                        if imgui.button(f'Send##{item.id},{tid[1]}') or entered:
                            # send
                            async_thread.run(task.send_input(self._computer_GUI_interactive_tasks[(item.id, tid[1])]+'\n',item,tsk))
                            self._computer_GUI_interactive_tasks[(item.id, tid[1])] = ''
                        if disabled:
                            utils.pop_disabled()
                    imgui.set_next_item_open(True, imgui.Cond_.once)
                    if imgui.collapsing_header(f'stdout##{tid[1]}'):
                        imgui.push_font(imgui_md.get_code_font())
                        imgui.text_wrapped(tsk.stdout)
                        imgui.pop_font()
                    imgui.set_next_item_open(True, imgui.Cond_.once)
                    if imgui.collapsing_header(f'stderr##{tid[1]}'):
                        imgui.push_font(imgui_md.get_code_font())
                        imgui.text_wrapped(tsk.stderr)
                        imgui.pop_font()
        imgui.end()


    def _computer_pane(self):
        # this pane is always visible, so we handle popups here
        self._fix_popup_transparency()
        open_popup_count = 0
        for popup in self.popup_stack:
            if hasattr(popup, "tick"):
                popup_func = popup.tick
            else:
                popup_func = popup
            opened, closed = popup_func()
            if closed:
                self.popup_stack.remove(popup)
            open_popup_count += opened
        # Popups are closed all at the end to allow stacking
        for _ in range(open_popup_count):
            imgui.end_popup()

        # now render actual pane
        if self.proj_select_state!=ActionState.Done:
            return

        if imgui.button('On'):
            utils.set_all(self.selected_computers, False)
            utils.set_all(self.selected_computers, True, predicate=lambda id: self.master.known_clients[id].client)
        utils.draw_hover_text('Select all running computers',text='')
        imgui.same_line()
        if imgui.button('Off'):
            utils.set_all(self.selected_computers, False)
            utils.set_all(self.selected_computers, True, predicate=lambda id: not self.master.known_clients[id].client)
        utils.draw_hover_text('Select all computers that are shut down',text='')
        imgui.same_line()
        if imgui.button('Invert'):
            new_vals = {k: not self.selected_computers[k] for k in self.selected_computers}
            self.selected_computers.clear()
            self.selected_computers |= new_vals
        utils.draw_hover_text('Invert selection of computers',text='')

        with self.master.known_clients_lock:
            if len(self.selected_computers)!=len(self.master.known_clients):
                # update: remove or add to selected as needed
                # NB: slightly complicated as we cannot replace the dict. A ref to it is
                # held by self.computer_lister, and that reffed object needs to be updated
                new_vals = {k:(self.selected_computers[k] if k in self.selected_computers else False) for k in self.master.known_clients}
                self.selected_computers.clear()
                self.selected_computers |= new_vals
            imgui.begin_child("##computer_list_frame", size=(0,-imgui.get_frame_height_with_spacing()), flags=imgui.WindowFlags_.horizontal_scrollbar)
            self.computer_lister.draw()
            imgui.end_child()