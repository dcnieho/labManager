import asyncio
import concurrent
import json
import time
import math
import sys
import platform
import webbrowser
import natsort
from dataclasses import dataclass, field

import imgui_bundle
from imgui_bundle import hello_imgui, icons_fontawesome, imgui, immapp, imspinner, imgui_md, imgui_color_text_edit, glfw_utils
from imgui_bundle import portable_file_dialogs
from imgui_bundle.demos_python import demo_utils
import glfw

from labManager.common import async_thread, config, eye_tracker, message, structs, task
from ... import master
from ._impl import computer_list, file_commander, filepicker, msgbox, utils

@dataclass
class History:
    pos     : int = -1
    items   : list[str] = field(default_factory=lambda: [''])   # last item (pos -1) is item currently being edited

class MainGUI:
    def __init__(self, mstr: master.Master = None, use_GUI_login=False):
        self.popup_stack = []

        if not mstr:
            self.master = master.Master()
            self.master.load_known_clients()
            self.master_provided_by_user = False
            self.use_GUI_login_flow = True          # ignored in this mode, but set for symmetry
        else:
            self.master = mstr
            self.master_provided_by_user = True
            self.use_GUI_login_flow = use_GUI_login # only for logging in, not for logging out
        # install hooks
        self.master.add_hook('client_disconnected', self._lost_client)
        self.master.add_hook('task_state_change', self._task_status_changed)
        if self.master_provided_by_user:
            # we need to listen to login or project selection state changes
            self.master.add_hook('login_state_change', self._login_state_change)
            self.master.add_hook('project_selection_state_change', self._projsel_state_change)
            self.master.add_hook('server_state_change', self._server_state_change)

        self.username         = ''
        self.password         = ''
        self.login_state      = structs.Status.Pending
        self.proj_select_state= structs.Status.Pending
        self.proj_idx         = -1
        self.project_select_immediately = False
        self.project          = ''  # NB: display name, self.master.project for real name
        self.no_login_mode    = False

        # GUI state
        self.running          = False
        self._window_list     = []
        self._to_dock         = []
        self._to_focus        = None
        self._need_set_window_title = False
        self._main_dock_node_id = None
        # debug window
        self.show_demo_window = False

        self.selected_computers: dict[int, bool] = {k:False for k in self.master.clients}
        # NB: use self.master.clients_lock also for self.selected_computers, extra safety
        self.computer_lister  = computer_list.ComputerList(self.master.clients, self.master.clients_lock, self.selected_computers, info_callback=self._open_computer_detail)

        # task GUI
        self._task_prep: task.TaskDef = task.TaskDef()
        self._task_history_payload: History = History()
        self._task_history_cwd: History = History()
        self._task_GUI_editor = imgui_color_text_edit.TextEditor()   # NB: also used for the payload display on a computer pane
        self._task_GUI_editor.set_language_definition(self._task_GUI_editor.LanguageDefinition.python())  # there is no batch, have to live with this...
        self._task_GUI_editor_copy_t = None
        self._task_GUI_open_file_diag = None
        self._task_GUI_cursor_pos = {'payload': 0, 'cwd': 0}
        self._task_GUI_selection_pos = {'payload': [0, 0], 'cwd': [0, 0]}

        # image management GUI
        self._images_list = []
        self._selected_image_id = None
        self._image_description_cache: dict[int, list[bool, str]] = {}
        self._active_imaging_tasks = []
        self._active_imaging_tasks_updater = None
        self._active_imaging_tasks_updater_should_stop = True
        self._active_upload_tasks = set()  # set of images
        self._active_upload_tasks_map: dict[str,int] = {}

        # file GUI
        self._file_commander: file_commander.FileCommander = None

        # computer detail GUIs
        self._computer_GUI_tasks: dict[int,list[str,int,int]|None] = {}
        self._computer_GUI_interactive_tasks: dict[tuple[int,int],str] = {}
        self._computer_GUI_interactive_history: dict[tuple[int,int],History] = {}
        self._computer_GUI_interactive_sent_finish: dict[tuple[int,int],bool] = {}
        self._computer_GUI_command_copy_t = None
        self._computer_GUI_cwd_copy_t = None

        # Show errors in threads
        def asyncexcepthook(future: asyncio.Future):
            try:
                exc = future.exception()
            except concurrent.futures.CancelledError:
                return
            if not exc:
                return
            tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
            if isinstance(exc, asyncio.TimeoutError):
                utils.push_popup(self, msgbox.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", msgbox.MsgBox.warn, more=tb)
                return
            utils.push_popup(self, msgbox.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", msgbox.MsgBox.error)
        async_thread.done_callback = asyncexcepthook


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
        runner_params.callbacks.before_exit = self._exiting

        # Status bar, idle throttling
        runner_params.imgui_window_params.show_status_bar = False
        runner_params.imgui_window_params.show_status_fps = False
        runner_params.fps_idling.enable_idling = False

        # Menu bar
        runner_params.imgui_window_params.show_menu_bar = True
        runner_params.callbacks.show_app_menu_items = self._show_app_menu_items
        runner_params.callbacks.show_menus = self._show_menu_gui


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

        # we use docking throughout this app just for resizable layouting and tab bars
        # set some flags so that users can't undock or see the menu arrow to hide the tab bar
        runner_params.docking_params.main_dock_space_node_flags = imgui.DockNodeFlags_.no_undocking | imgui.internal.DockNodeFlagsPrivate_.no_docking | imgui.internal.DockNodeFlagsPrivate_.no_window_menu_button

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
        self.computer_list.gui_function = self._computer_list_pane
        self.computer_list.can_be_closed = False

        # Finally, transmit these windows to HelloImGui
        runner_params.docking_params.dockable_windows = [
            self.computer_list,
            self._make_main_space_window("Login", self._login_GUI),
        ]

        if self.master_provided_by_user:
            # user passed in an existing master. Figure out what state we are in
            if self.master.username and self.master.password:
                self.username       = self.master.username
                self.password       = self.master.password
                self._login_done()
            if self.master.project:
                self.proj_idx = list(self.master.projects.keys()).index(self.master.project)
                self._project_selected()
            # check if running without login
            self.no_login_mode = self.proj_select_state==structs.Status.Pending and self.master.is_serving()
            if self.no_login_mode:
                self._continue_without_login()

        ################################################################################################
        # Part 3: Run the app
        ################################################################################################
        addons_params = immapp.AddOnsParams()
        addons_params.with_markdown = True
        immapp.run(runner_params, addons_params)

    def quit(self):
        hello_imgui.get_runner_params().app_shall_exit = True

    def _exiting(self):
        self.running = False
        self._logout()

    def _update_windows(self):
        if not self.running:
            # apply theme
            hello_imgui.apply_theme(hello_imgui.ImGuiTheme_.darcula_darker)
            # fix up the style: fully opaque window backgrounds
            window_bg = imgui.get_style().color_(imgui.Col_.window_bg)
            window_bg.w = 1
        self.running = True
        if self._need_set_window_title:
            self._set_window_title()
        # update windows to be shown
        if self._window_list:
            hello_imgui.get_runner_params().docking_params.dockable_windows = self._window_list
            self._window_list = []
        else:
            # check if any computer detail windows were closed. Those should be removed from the list
            hello_imgui.get_runner_params().docking_params.dockable_windows = \
                [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if w.is_visible or (not w.label.endswith('computer_view') and not w.label.endswith('file_commander'))]

        # we also handle docking requests here, once we can (need self._main_dock_node_id)
        if self._to_dock and self._main_dock_node_id:
            for w in self._to_dock:
                imgui.internal.dock_builder_dock_window(w, self._main_dock_node_id)
            self._to_dock = []

        # handle focus requests, which apparently need to be delayed
        # one frame for them to work also in case its a new window
        if self._to_focus is not None:
            if isinstance(self._to_focus,str):
                self._to_focus = [self._to_focus,1]
            if self._to_focus[1]>0:
                self._to_focus[1] -= 1
            else:
                for w in hello_imgui.get_runner_params().docking_params.dockable_windows:
                    if w.label==self._to_focus[0]:
                        w.focus_window_at_next_frame = True
                self._to_focus = None

    def _make_main_space_window(self, name, gui_func, can_be_closed=False):
        main_space_view = hello_imgui.DockableWindow()
        main_space_view.label = name
        main_space_view.dock_space_name = "MainDockSpace"
        main_space_view.gui_function = gui_func
        main_space_view.can_be_closed = can_be_closed
        return main_space_view

    def _show_app_menu_items(self):
        if imgui.begin_menu("Open project", self.login_state==structs.Status.Finished and not self.master_provided_by_user):
            for i,(p,pn) in enumerate(self.master.projects.items()):
                if self.proj_select_state==structs.Status.Finished and i==self.proj_idx:
                    # don't show currently loaded project in menu
                    continue
                lbl = p if pn==p else f'{p} ({pn})'
                if imgui.menu_item(lbl, "", False)[0]:
                    self._unload_project()
                    self.proj_idx = i
                    self.project_select_immediately = True
            imgui.end_menu()
        if imgui.menu_item("Close project", "", False, enabled=self.proj_select_state==structs.Status.Finished and not self.master_provided_by_user)[0]:
            self._unload_project()
        if self.no_login_mode:
            if imgui.menu_item("Return to login", "", False, enabled=not self.master_provided_by_user)[0]:
                self._logout()
        else:
            if imgui.menu_item("Log out", "", False, enabled=self.login_state==structs.Status.Finished and not self.master_provided_by_user)[0]:
                self._logout()

    def _show_menu_gui(self):
        if imgui.begin_menu("Help"):
            if imgui.menu_item("About", "", False)[0]:
                utils.push_popup(self, self._draw_about_popup)
            self.show_demo_window = imgui.menu_item("Debug window", "", self.show_demo_window)[1]
            imgui.end_menu()

    def _get_window_title(self, add_user=False, add_project=False, no_login_mode=False):
        title = "labManager Master"
        if no_login_mode:
            title+= ' (not logged in)'
        elif add_user and add_project:
            title+= f' ({self.username}/{self.project})'
        elif add_user:
            title+= f' ({self.username})'
        return title

    def _set_window_title(self):
        new_title = self._get_window_title(
            self.login_state==structs.Status.Finished,
            self.proj_select_state==structs.Status.Finished,
            self.no_login_mode)
        # this is just for show, doesn't trigger an update. But lets keep them in sync
        hello_imgui.get_runner_params().app_window_params.window_title = new_title
        # actually update window title
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_window_title(win, new_title)
        self._need_set_window_title = False

    def _login_done(self):
        self.login_state = structs.Status.Finished
        self._need_set_window_title = True

    def _logout(self):
        self._unload_project()

        self.username         = ''
        self.password         = ''
        self.login_state      = structs.Status.Pending
        self.no_login_mode    = False
        if not self.master_provided_by_user:
            self.master.logout()
        self._need_set_window_title = True

    def _continue_without_login(self):
        self.no_login_mode = True
        self._window_list = [
            self.computer_list,
            self._make_main_space_window("Tasks", self._task_GUI),
            self._make_main_space_window("File Management", self._file_GUI),
            ]
        self._to_focus = "Tasks"
        self._to_dock = ["Tasks", "File Management"]
        self._need_set_window_title = True
        # start server
        if not self.master_provided_by_user or self.use_GUI_login_flow:
            async_thread.run(self.master.start_server())

    def _project_selected(self):
        self.proj_select_state = structs.Status.Finished
        # update GUI
        project = list(self.master.projects.keys())[self.proj_idx]
        self.project = self.master.projects[project]
        self.computer_lister.set_project(self.project)
        if self.project!=project:
            self.project = f'{project} ({self.project})'
        self._window_list = [
            self.computer_list,
            self._make_main_space_window("Tasks", self._task_GUI),
            self._make_main_space_window("Image Management", self._imaging_GUI),
            self._make_main_space_window("File Management", self._file_GUI),
            ]
        self._to_focus = "Tasks"
        self._to_dock = ["Tasks", "Image Management", "File Management"]
        self._need_set_window_title = True
        # prep for image management
        async_thread.run(self._get_project_images())
        # start server
        if not self.master_provided_by_user or self.use_GUI_login_flow:
            async_thread.run(self.master.start_server())

    async def _get_project_images(self):
        n_tries = 0
        max_tries = 3
        while n_tries<max_tries:
            n_tries += 1
            try:
                temp_list = await self.master.toems_get_disk_images()
                # get extra info about disk images
                coros = []
                for im in temp_list:
                    coros.append(self.master.toems_get_disk_image_info(im['Id']))
                    coros.append(self.master.toems_get_disk_image_size(im['Id']))
                res   = await asyncio.gather(*coros)
                infos = res[0::2]
                dss   = res[1::2]
                # add to output
                for im,info,ds in zip(temp_list,infos,dss):
                    im['DiskSize'] = ds
                    im['TimeStamp'] = info['TimeStamp'] if info is not None else 'Unknown'
                    im['SourceComputer'] = info['SourceComputer'] if info is not None else 'Unknown'
                # atomic update so we can't read incomplete state elsewhere
                self._images_list = temp_list
                # also dump potentially stale image description cache
                for im in self._images_list:
                    if im['Id'] in self._image_description_cache:
                        if not self._image_description_cache[im['Id']][0] or self._image_description_cache[im['Id']][1]==im['Description']:
                            # was not edited or current description matches cache, dump
                            del self._image_description_cache[im['Id']]
                # succeeded, break out of the try-loop
                break
            except Exception as exc:
                if n_tries>=max_tries:
                    raise exc


    def _unload_project(self):
        if not self.master_provided_by_user:
            self.master.unset_project()
        with self.master.clients_lock:
            self.selected_computers.clear()
            self.selected_computers |= {k:False for k in self.master.clients}
        self._selected_image_id = None
        self._active_imaging_tasks_updater_should_stop = True
        self._images_list       = []
        self.proj_select_state  = structs.Status.Pending
        self.proj_idx           = -1
        self.project            = ''
        self.computer_lister.set_project(self.project)

        # reset GUI
        self._task_prep = task.TaskDef()
        self._task_history_payload = History()
        self._task_history_cwd = History()
        self._window_list = [self.computer_list, self._make_main_space_window("Login", self._login_GUI)]
        self._need_set_window_title = True

    def _login_GUI(self):
        if not self._main_dock_node_id:
            # this window is docked to the right dock node, if we don't
            # have it yet, query id of this dock node as we'll need it for later
            # windows
            self._main_dock_node_id = imgui.get_window_dock_id()
        global_disabled = self.master_provided_by_user and not self.use_GUI_login_flow
        if global_disabled:
            utils.push_disabled()
        if self.login_state != structs.Status.Finished:
            local_disabled = self.login_state==structs.Status.Running and not global_disabled
            if local_disabled:
                utils.push_disabled()
            if 'login' in config.master:
                i1,self.username = imgui.input_text_with_hint('User name',config.master['login']['hint'], self.username, flags=imgui.InputTextFlags_.enter_returns_true)
            else:
                i1,self.username = imgui.input_text          ('User name',                                self.username, flags=imgui.InputTextFlags_.enter_returns_true)
            i2,self.password = imgui.input_text('Password', self.password, flags=imgui.InputTextFlags_.enter_returns_true|imgui.InputTextFlags_.password)

            if self.login_state==structs.Status.Running:
                symbol_size = imgui.calc_text_size("x").y
                spinner_radii = [x/22*symbol_size for x in [22, 16, 10]]
                lw = 3.5/22*symbol_size
                imspinner.spinner_ang_triple(f'loginSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
            else:
                if imgui.button("Log in") | i1 | i2:
                    if not self.username:
                        utils.push_popup(self, msgbox.msgbox, "Login error", 'Fill in a username', msgbox.MsgBox.error)
                    else:
                        self.login_state = structs.Status.Running
                        async_thread.run(self.master.login(self.username,self.password), lambda fut: self._login_projectsel_result('login',fut))
                imgui.same_line()
                if imgui.button("Continue without logging in"):
                    self._continue_without_login()

            if local_disabled:
                utils.pop_disabled()
        else:
            if not self.master.projects:
                imgui.text_colored((1.,0,0,1.), f'There are no projects for user {self.username}')
                if imgui.button("Log out"):
                    self._logout()
            else:
                # if we have preselected a project (e.g. through change project menu),
                # start the loading action immediately
                if self.project_select_immediately:
                    self._do_select_project()
                    self.project_select_immediately = False
                local_disabled = self.proj_select_state==structs.Status.Running and not global_disabled
                if local_disabled:
                    utils.push_disabled()
                imgui.text('Select project:')
                projects = []
                for p,pn in self.master.projects.items():
                    if pn==p:
                        projects.append(p)
                    else:
                        projects.append(f'{p} ({pn})')
                _,self.proj_idx = imgui.list_box('##Project', 0 if self.proj_idx==-1 else self.proj_idx, projects)
                # select on double-click or press of enter key
                selection_done = (imgui.is_item_clicked() and imgui.is_mouse_double_clicked(imgui.MouseButton_.left)) or imgui.is_key_pressed(imgui.Key.enter)

                if self.proj_select_state==structs.Status.Running:
                    symbol_size = imgui.calc_text_size("x").y
                    spinner_radii = [x/22*symbol_size for x in [22, 16, 10]]
                    lw = 3.5/22*symbol_size
                    imspinner.spinner_ang_triple(f'projSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
                else:
                    if imgui.button("Select") or selection_done:
                        self._do_select_project()

                if local_disabled:
                    utils.pop_disabled()
        if global_disabled:
            utils.pop_disabled()
    def _do_select_project(self):
        self.proj_select_state = structs.Status.Running
        async_thread.run(self.master.set_project(list(self.master.projects.keys())[self.proj_idx]), lambda fut: self._login_projectsel_result('project',fut))

    def _login_projectsel_result(self, stage, future: asyncio.Future):
        try:
            exc = future.exception()
        except concurrent.futures.CancelledError:
            return
        if not exc:
            # log in or project selection successful
            if stage=='login':
                self._login_done()
            elif stage=='project' and not self.master_provided_by_user:
                # NB: if self.master_provided_by_user, _project_selected() is called by
                # _projsel_state_change() already
                self._project_selected()
            return

        # error occurred
        if stage=='login':
            self.login_state = structs.Status.Pending
        elif stage=='project':
            self.proj_select_state = structs.Status.Pending
        self._show_login_projectsel_error(exc, stage)

    def _show_login_projectsel_error(self, exc: Exception, stage: str):
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

    # NB: these three hooks are only attached when user passed in an existing master that
    # we are observing. We're listening to potentially relevant changes and update the GUI
    # state if needed
    def _login_state_change(self, status: structs.Status, error: Exception|None = None):
        match status:
            case structs.Status.Pending:
                # not logged in (e.g. just logged out)
                self._logout()
            case structs.Status.Running:
                # busy logging in
                self.login_state = structs.Status.Running
            case structs.Status.Finished:
                # now logged in
                self.username       = self.master.username
                self.password       = self.master.password
                self._login_done()
            case structs.Status.Errored:
                # an error occured when logging in, show it
                self._show_login_projectsel_error(error, 'login')
    def _projsel_state_change(self, status: structs.Status, error: Exception|None = None):
        match status:
            case structs.Status.Pending:
                # no project loaded (e.g. just unloaded)
                self._unload_project()
            case structs.Status.Running:
                # busy loading project
                self.proj_select_state = structs.Status.Running
            case structs.Status.Finished:
                # project now loaded
                self.proj_idx = list(self.master.projects.keys()).index(self.master.project)
                self._project_selected()
            case structs.Status.Errored:
                # an error occured when loading a project, show it
                self._show_login_projectsel_error(error, 'project')
    def _server_state_change(self, status: structs.Status):
        if status==structs.Status.Running:
            # check if running without login
            self.no_login_mode = self.proj_select_state==structs.Status.Pending
            if self.no_login_mode:
                self._continue_without_login()
        elif status==structs.Status.Pending and self.no_login_mode:
            self._logout()

    def _lost_client(self, client: structs.ConnectedClient, client_id: int):
        # we lost this client. Clear out all state related to it
        if self.master.clients[client_id].known:
            self._computer_GUI_tasks[client_id] = None
        else:
            del self._computer_GUI_tasks[client_id]
            self._window_list = [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if not w.label.startswith(self.master.clients[client_id].name)]

        to_del = []
        for t in self._computer_GUI_interactive_tasks:
            if t[0]==client_id:
                to_del.append(t)
        for t in to_del:
            del self._computer_GUI_interactive_tasks[t]

        to_del = []
        for t in self._computer_GUI_interactive_sent_finish:
            if t[0]==client_id:
                to_del.append(t)
        for t in to_del:
            del self._computer_GUI_interactive_sent_finish[t]

        to_del = []
        for t in self._computer_GUI_interactive_history:
            if t[0]==client_id:
                to_del.append(t)
        for t in to_del:
            del self._computer_GUI_interactive_history[t]

    def _task_status_changed(self, _, client_id: int, tsk: task.Task):
        key = (client_id, tsk.id)
        if tsk.status in [structs.Status.Finished, structs.Status.Errored]:
            if key in self._computer_GUI_interactive_tasks:
                del self._computer_GUI_interactive_tasks[key]
            if key in self._computer_GUI_interactive_sent_finish:
                del self._computer_GUI_interactive_sent_finish[key]
            if key in self._computer_GUI_interactive_history:
                del self._computer_GUI_interactive_history[key]

    def _draw_about_popup(self):
        def popup_content():
            _60 = 60*hello_imgui.dpi_window_size_factor()
            _230 = 230*hello_imgui.dpi_window_size_factor()
            width = 530*hello_imgui.dpi_window_size_factor()
            imgui.begin_group()
            imgui.dummy((_60, _230))
            imgui.same_line()
            imgui.dummy((_230, _230))
            #_general_imgui.icon_texture.render(_230, _230, rounding=globals.settings.style_corner_radius)
            imgui.same_line()
            imgui.begin_group()
            imgui.push_text_wrap_pos(width - imgui.get_style().frame_padding.x)
            #imgui.push_font(self.big_font)
            imgui.text("labManager")
            #imgui.pop_font()
            imgui.text(f"Version {master.__version__}")
            imgui.text("Made by Diederick C. Niehorster")
            imgui.text("")
            imgui.text(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
            imgui.text(f"GLFW {'.'.join(str(num) for num in glfw.get_version())}, pyGLFW {glfw.__version__}")
            imgui.text(f"ImGui {imgui.get_version()}, imgui_bundle {imgui_bundle.__version__}")
            if sys.platform.startswith("linux"):
                imgui.text(f"{platform.system()} {platform.release()}")
            elif sys.platform.startswith("win"):
                imgui.text(f"{platform.system()} {platform.release()} {platform.version()}")
            elif sys.platform.startswith("darwin"):
                imgui.text(f"{platform.system()} {platform.release()}")
            imgui.pop_text_wrap_pos()
            imgui.end_group()
            imgui.same_line()
            imgui.dummy((width-imgui.get_cursor_pos_x(), _230))
            imgui.end_group()
            imgui.spacing()
            btn_tot_width = (width - 2*imgui.get_style().item_spacing.x)
            if imgui.button("PyPI", size=(btn_tot_width/6, 0)):
                webbrowser.open("https://pypi.org/project/labManager-master/")
            imgui.same_line()
            utils.push_disabled()
            if imgui.button("Paper", size=(btn_tot_width/6, 0)):
                pass
            utils.pop_disabled()
            imgui.same_line()
            if imgui.button("GitHub repo", size=(btn_tot_width/3, 0)):
                webbrowser.open("https://github.com/dcnieho/labManager")
            imgui.same_line()
            if imgui.button("Researcher homepage", size=(btn_tot_width/3, 0)):
                webbrowser.open("https://scholar.google.se/citations?user=uRUYoVgAAAAJ&hl=en")

            imgui.spacing()
            imgui.spacing()
            imgui.push_text_wrap_pos(width - imgui.get_style().frame_padding.x)
            imgui.text("This software is licensed under the MIT license and is provided to you for free. Furthermore, due to "
                       "its license, it is also free as in freedom: you are free to use, study, modify and share this software "
                       "in whatever way you wish as long as you keep the same license.")
            imgui.spacing()
            imgui.spacing()
            imgui.text("If you find bugs or have some feedback, please do let me know on GitHub (using issues or pull requests).")
            imgui.spacing()
            imgui.spacing()
            imgui.dummy((0, 10*hello_imgui.dpi_window_size_factor()))
            #imgui.push_font(self.big_font)
            size = imgui.calc_text_size("Reference")
            imgui.set_cursor_pos_x((width - size.x + imgui.get_style().scrollbar_size) / 2)
            imgui.text("Reference")
            #imgui.pop_font()
            imgui.spacing()
            imgui.spacing()
            reference         = r"Niehorster, D.C., Gullberg, M. & Nyström, M. (in prep). Designing and running multi-user lab environments for behavioral science: infrastructure, open-source tools and practical advice."
            reference_bibtex  = r"""@article{niehorster2024labmanager,
    Author = {Niehorster, Diederick C. and Gullberg, Marianne and Nystr{\"o}m, Marcus},
    Journal = {},
    Number = {},
    Pages = {},
    Title = {Designing and running multi-user lab environments for behavioral science: infrastructure, open-source tools and practical advice},
    Year = {in prep}
}
"""
            imgui.text(reference)
            if imgui.begin_popup_context_item(f"##reference_context"):
                if imgui.selectable("APA", False)[0]:
                    imgui.set_clipboard_text(reference)
                if imgui.selectable("BibTeX", False)[0]:
                    imgui.set_clipboard_text(reference_bibtex)
                imgui.end_popup()
            utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")

            imgui.pop_text_wrap_pos()
        return utils.popup("About labManager", popup_content, closable=True, outside=True)

    def _set_task_prep(self, tsk: task.TaskDef | task.Task):
        self._task_prep = tsk if isinstance(tsk, task.TaskDef) else task.TaskDef.fromtask(tsk)
        self._task_GUI_cursor_pos['payload'] = 0
        self._task_GUI_selection_pos['payload'] = [0, 0]
        self._task_GUI_cursor_pos['cwd'] = 0
        self._task_GUI_selection_pos['cwd'] = [0, 0]
        if self._task_prep.type in [task.Type.Shell_command, task.Type.Process_exec, task.Type.Python_module]:
            self._task_history_payload.items[-1] = self._task_prep.payload_text
            self._task_history_payload.pos = -1
        if self._task_prep.type!=task.Type.Wake_on_LAN:
            self._task_history_cwd.items[-1] = self._task_prep.cwd
            self._task_history_cwd.pos = -1

    def _task_GUI(self):
        if not self._main_dock_node_id:
            # this window is docked to the right dock node, if we don't
            # have it yet, query id of this dock node as we'll need it for later
            # windows
            self._main_dock_node_id = imgui.get_window_dock_id()
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

            task_GUI_list_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(dock_space_id, imgui.Dir_.left,0.15)
            task_GUI_type_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.left,.15/(1-.15))
            task_GUI_details_dock_id,_,task_GUI_action_dock_id = \
                imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.up,0.90)
            # make sure you can't dock in these
            node = imgui.internal.dock_builder_get_node(task_GUI_list_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(task_GUI_type_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(task_GUI_details_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(task_GUI_action_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)

            imgui.internal.dock_builder_dock_window('task_list_pane', task_GUI_list_dock_id)
            imgui.internal.dock_builder_dock_window('task_type_pane', task_GUI_type_dock_id)
            imgui.internal.dock_builder_dock_window('task_config_pane', task_GUI_details_dock_id)
            imgui.internal.dock_builder_dock_window('task_confirm_pane', task_GUI_action_dock_id)
            imgui.internal.dock_builder_finish(dock_space_id)
        imgui.dock_space(dock_space_id, (0.,0.), imgui.DockNodeFlags_.no_docking_split|imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)

        if imgui.begin('task_list_pane'):
            if 'tasks' in config.master:
                for t in config.master['tasks']:
                    if imgui.button(t.name):
                        self._set_task_prep(t)
            else:
                imgui.text_wrapped('There are no preconfigured tasks. Launch your own task using the panels on the right.')
        imgui.end()
        if imgui.begin('task_type_pane'):
            for t in task.Type:
                if imgui.radio_button(t.value, self._task_prep.type==t):
                    old_type = self._task_prep.type
                    self._task_prep.type = t
                    # remove command etc if not wanted
                    if t==task.Type.Wake_on_LAN:
                        self._task_prep = task.TaskDef()
                        self._task_prep.type = task.Type.Wake_on_LAN
                    # make sure we don't have a multiline commands in a single-line
                    # textbox
                    if old_type in [task.Type.Batch_file, task.Type.Python_script]:
                        if t not in [task.Type.Batch_file, task.Type.Python_script]:
                            self._task_prep.payload_type = 'text'
                            self._task_prep.payload_text = utils.trim_str(self._task_prep.payload_text)
                            self._task_GUI_cursor_pos['payload'] = 0
                            self._task_GUI_selection_pos['payload'] = [0, 0]
                    if t in [task.Type.Shell_command, task.Type.Process_exec, task.Type.Python_module]:
                        self._task_history_payload.items[-1] = self._task_prep.payload_text
                        self._task_history_payload.pos = -1
                    if t!=task.Type.Wake_on_LAN:
                        self._task_history_cwd.items[-1] = self._task_prep.cwd
                        self._task_history_cwd.pos = -1
                utils.draw_hover_text(t.doc, text='')
        imgui.end()
        enter_pressed = False
        if imgui.begin('task_config_pane'):
            def edit_callback(this: MainGUI, which: str, data: imgui.InputTextCallbackData):
                # always track cursor and selection
                this._task_GUI_cursor_pos[which] = data.cursor_pos
                this._task_GUI_selection_pos[which] = sorted([data.selection_start, data.selection_end])
                # deal with history
                if which=='payload':
                    hist = this._task_history_payload
                elif which=='cwd':
                    hist = this._task_history_cwd
                if data.event_flag==imgui.InputTextFlags_.callback_edit:
                    # store current state to item -1 (the item currently being edited)
                    # NB: this means that upon history navigation, current item is only replaced once a history item is edited
                    hist.items[-1] = data.buf
                    hist.pos = -1
                elif data.event_flag==imgui.InputTextFlags_.callback_history:
                    # replace current buffer with history
                    new_hist_pos = hist.pos
                    if data.event_key==imgui.Key.up_arrow:
                        if new_hist_pos == -1:
                            new_hist_pos = len(hist.items)-1 - 1    # -1 for indexing, -1 for going one back in history
                        elif new_hist_pos>0:
                            new_hist_pos -= 1
                    elif data.event_key==imgui.Key.down_arrow:
                        if new_hist_pos != -1:
                            new_hist_pos += 1
                            if new_hist_pos==len(hist.items)-1:
                                new_hist_pos = -1
                    if new_hist_pos != hist.pos:
                        hist.pos = new_hist_pos
                        data.delete_chars(0, data.buf_text_len)
                        data.insert_chars(0, hist.items[hist.pos])
                return 0
            def insert_path(this: MainGUI, which: str, path: str):
                tsk = this._task_prep
                if which=='payload':
                    txt = tsk.payload_text
                elif which=='cwd':
                    txt = tsk.cwd
                if isinstance(self._task_GUI_cursor_pos[which], list):
                    # command_editor, has a row:column position
                    lines = txt.splitlines(keepends=True)
                    if this._task_GUI_editor.has_selection():
                        # convert to indices into the whole string
                        s1 = this._task_GUI_selection_pos[which][0]
                        s1 = sum([len(x) for x in lines[0:s1[0]]])+s1[1]
                        s2 = this._task_GUI_selection_pos[which][1]
                        s2 = sum([len(x) for x in lines[0:s2[0]]])+s2[1]
                        txt = tsk.payload_text[0:s1] + txt[s2:]
                        this._task_GUI_cursor_pos[which] = this._task_GUI_selection_pos[which][0] # would be at wrong position if selection was grown right-to-left
                        this._task_GUI_editor.clear_selections()
                    # convert to indices into the whole string
                    cp = this._task_GUI_cursor_pos[which]
                    cp = sum([len(x) for x in lines[0:cp[0]]])+cp[1]
                    txt = txt[:cp] + str(path[0]) + txt[cp:]
                else:
                    # normal edit box
                    if this._task_GUI_selection_pos[which][1]!=this._task_GUI_selection_pos[which][0]:
                        # selection, remove that part of the text
                        txt = tsk.payload_text[0:this._task_GUI_selection_pos[which][0]] + txt[this._task_GUI_selection_pos[which][1]:]
                        this._task_GUI_cursor_pos[which] = this._task_GUI_selection_pos[which][0] # would be at wrong position if selection was grown right-to-left
                    txt = txt[:this._task_GUI_cursor_pos[which]] + str(path[0]) + txt[this._task_GUI_cursor_pos[which]:]
                if which=='payload':
                    tsk.payload_text = txt
                elif which=='cwd':
                    tsk.cwd = txt

            if self._task_prep.type==task.Type.Wake_on_LAN:
                imgui.text('Wake on LAN action has no parameters')
            else:
                use_code_editor = False
                can_select_payload_type = False
                match self._task_prep.type:
                    case task.Type.Shell_command:
                        field_name = 'Command'
                    case task.Type.Process_exec:
                        field_name = 'Executable and arguments'
                    case task.Type.Batch_file:
                        if self._task_prep.payload_type=='text':
                            field_name = 'Batch file contents'
                            use_code_editor = True
                        else:
                            field_name = 'Batch file'
                        can_select_payload_type = True
                    case task.Type.Python_module:
                        field_name = 'Python module'
                    case task.Type.Python_script:
                        if self._task_prep.payload_type=='text':
                            field_name = 'Python script contents'
                            use_code_editor = True
                        else:
                            field_name = 'Python script'
                        can_select_payload_type = True
                if can_select_payload_type:
                    if imgui.radio_button('text', self._task_prep.payload_type=='text'):
                        self._task_prep.payload_type='text'
                    imgui.same_line()
                    if imgui.radio_button('file', self._task_prep.payload_type=='file'):
                        self._task_prep.payload_type='file'

                width = imgui.get_content_region_avail().x
                if self._task_prep.payload_type=='text':
                    if use_code_editor:
                        # based on immapp.snippets.show_code_snippet
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
                        imgui.same_line()
                        if imgui.small_button("Insert path##payload"):
                            fap = filepicker.FileActionProvider(network=config.master['network'], master=self.master)
                            with self.master.clients_lock:
                                clients = [self.master.clients[cid].name for cid in self.master.clients if self.master.clients[cid].online]
                            client_name = clients[0] if clients else None
                            utils.push_popup(self, filepicker.FilePicker(title='Select path to insert', start_machine=client_name, allow_multiple=False, file_action_provider=fap, callback=lambda path: insert_path(self, 'payload', path)))

                        text_x = top_right.x - line_height * 6.
                        imgui.set_cursor_pos((text_x, text_y))
                        cursor_pos = self._task_GUI_editor.get_cursor_position()
                        imgui.text(f"L:{cursor_pos.m_line+1:3d} C:{cursor_pos.m_column+1:3d}")
                        s1 = self._task_GUI_editor.get_selection_start()
                        s2 = self._task_GUI_editor.get_selection_end()
                        self._task_GUI_cursor_pos['payload'] = [cursor_pos.m_line, cursor_pos.m_column]
                        self._task_GUI_selection_pos['payload'] = [[s1.m_line, s1.m_column], [s2.m_line, s2.m_column]]

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
                        imgui.same_line()
                        if imgui.small_button("Insert path##payload"):
                            fap = filepicker.FileActionProvider(network=config.master['network'], master=self.master)
                            with self.master.clients_lock:
                                clients = [self.master.clients[cid].name for cid in self.master.clients if self.master.clients[cid].online]
                            client_name = clients[0] if clients else None
                            utils.push_popup(self, filepicker.FilePicker(title='Select path to insert', start_machine=client_name, allow_multiple=False, file_action_provider=fap, callback=lambda path: insert_path(self, 'payload', path)))
                        if self._task_history_payload.pos==-1 and self._task_prep.payload_text != self._task_history_payload.items[-1]:
                            self._task_history_payload.items[-1] = self._task_prep.payload_text
                        imgui.set_next_item_width(width-imgui.get_frame_height_with_spacing())    # space for arrow button
                        imgui.push_font(imgui_md.get_code_font())
                        enter_pressed, self._task_prep.payload_text = imgui.input_text(f'##{field_name}', self._task_prep.payload_text, flags=imgui.InputTextFlags_.enter_returns_true|imgui.InputTextFlags_.callback_always|imgui.InputTextFlags_.callback_edit|imgui.InputTextFlags_.callback_history, callback=lambda x: edit_callback(self, 'payload', x))
                        imgui.pop_font()
                        imgui.same_line()
                        disabled = len(self._task_history_payload.items)<=1
                        if disabled:
                            utils.push_disabled()
                        button_pos = imgui.get_cursor_screen_pos()
                        if imgui.arrow_button('##payload_history_button', imgui.Dir_.down):
                            imgui.set_next_window_pos([x+y for x,y in zip(button_pos,(0,imgui.get_frame_height_with_spacing()))])
                            imgui.open_popup('##payload_history_popup')
                        if imgui.begin_popup('##payload_history_popup'):
                            idx = self._task_history_payload.pos
                            if idx==-1:
                                idx = len(self._task_history_payload.items)-1
                            changed, idx = imgui.list_box('##payload_history_popup_select',idx,self._task_history_payload.items)
                            if changed:
                                if idx==len(self._task_history_payload.items)-1:
                                    self._task_history_payload.pos = -1
                                else:
                                    self._task_history_payload.pos = idx
                                self._task_prep.payload_text = self._task_history_payload.items[idx]
                                imgui.close_current_popup()
                            imgui.end_popup()
                        if disabled:
                            utils.pop_disabled()
                else:
                    imgui.push_font(imgui_md.get_code_font())
                    imgui.set_next_item_width(width)
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
                imgui.text('Working directory')
                is_hovered = imgui.is_item_hovered()
                imgui.same_line()
                if imgui.small_button("Insert path##cwd"):
                    fap = filepicker.FileActionProvider(network=config.master['network'], master=self.master)
                    with self.master.clients_lock:
                        clients = [self.master.clients[cid].name for cid in self.master.clients if self.master.clients[cid].online]
                    client_name = clients[0] if clients else None
                    utils.push_popup(self, filepicker.FilePicker(title='Select path to insert', start_machine=client_name, allow_multiple=False, file_action_provider=fap, callback=lambda path: insert_path(self, 'cwd', path)))
                if self._task_history_cwd.pos==-1 and self._task_prep.cwd != self._task_history_cwd.items[-1]:
                    self._task_history_cwd.items[-1] = self._task_prep.cwd
                imgui.set_next_item_width(width-imgui.get_frame_height_with_spacing())    # space for arrow button
                imgui.push_font(imgui_md.get_code_font())
                enter_pressed2, self._task_prep.cwd = imgui.input_text('##cwd', self._task_prep.cwd, flags=imgui.InputTextFlags_.enter_returns_true|imgui.InputTextFlags_.callback_always|imgui.InputTextFlags_.callback_edit|imgui.InputTextFlags_.callback_history, callback=lambda x: edit_callback(self, 'cwd', x))
                enter_pressed = enter_pressed or enter_pressed2
                imgui.pop_font()
                if (is_hovered or imgui.is_item_hovered()):
                    utils.draw_tooltip('Working directory from which the command will be executed')
                imgui.same_line()
                disabled = len(self._task_history_cwd.items)<=1
                if disabled:
                    utils.push_disabled()
                button_pos = imgui.get_cursor_screen_pos()
                if imgui.arrow_button('##cwd_history_button', imgui.Dir_.down):
                    imgui.set_next_window_pos([x+y for x,y in zip(button_pos,(0,imgui.get_frame_height_with_spacing()))])
                    imgui.open_popup('##cwd_history_popup')
                if imgui.begin_popup('##cwd_history_popup'):
                    idx = self._task_history_cwd.pos
                    if idx==-1:
                        idx = len(self._task_history_cwd.items)-1
                    changed, idx = imgui.list_box('##cwd_history_popup_select',idx,self._task_history_cwd.items)
                    if changed:
                        if idx==len(self._task_history_cwd.items)-1:
                            self._task_history_cwd.pos = -1
                        else:
                            self._task_history_cwd.pos = idx
                        self._task_prep.cwd = self._task_history_cwd.items[idx]
                        imgui.close_current_popup()
                    imgui.end_popup()
                if disabled:
                    utils.pop_disabled()
                _, self._task_prep.interactive = imgui.checkbox('Interactive', self._task_prep.interactive)
                utils.draw_hover_text('If enabled, it is possible to send input (stdin) to the running command',text='')
                if self._task_prep.type in [task.Type.Python_module, task.Type.Python_script]:
                    _, self._task_prep.python_unbuf = imgui.checkbox('Unbuffered mode', self._task_prep.python_unbuf)
                    utils.draw_hover_text('If enabled, the "-u" switch is specified for the python call, so that all output of the process is directly visible in the task result view',text='')
        imgui.end()
        if imgui.begin('task_confirm_pane'):
            with self.master.clients_lock:
                if self._task_prep.type==task.Type.Wake_on_LAN:
                    selected_clients = [cid for cid in self.selected_computers if self.selected_computers[cid] and cid in self.master.clients and not self.master.clients[cid].online]
                else:
                    selected_clients = [cid for cid in self.selected_computers if self.selected_computers[cid] and cid in self.master.clients and self.master.clients[cid].online]
            disabled1 = not selected_clients
            if self._task_prep.type==task.Type.Wake_on_LAN:
                disabled2 = False
            elif self._task_prep.payload_type=='text':
                disabled2 = not self._task_prep.payload_text
            else:
                disabled2 = not self._task_prep.payload_file
            disabled = disabled1 or disabled2
            if disabled:
                utils.push_disabled()
            do_clear = False
            if imgui.button("Run") or (not disabled and enter_pressed):
                async_thread.run(
                    self.master.run_task(
                        self._task_prep.type,
                        self._task_prep.payload_text if self._task_prep.payload_type=='text' else self._task_prep.payload_file,
                        selected_clients,
                        self._task_prep.payload_type,
                        self._task_prep.cwd,
                        self._task_prep.env,
                        self._task_prep.interactive,
                        self._task_prep.python_unbuf
                    )
                )
                # deal with history
                if self._task_prep.type in [task.Type.Shell_command, task.Type.Process_exec, task.Type.Python_module]:
                    self._task_history_payload.items[-1] = self._task_prep.payload_text    # should be equal, but lets be sure
                    if len(self._task_history_payload.items)>1 and self._task_history_payload.items[-1]==self._task_history_payload.items[-2]:
                        # if command same as previous, don't add (collapse history)
                        self._task_history_payload.items[-1] = ''
                    else:
                        self._task_history_payload.items.append('')   # new command about to be edited
                if self._task_prep.type!=task.Type.Wake_on_LAN:
                    self._task_history_cwd.items[-1] = self._task_prep.cwd    # should be equal, but lets be sure
                    if not self._task_prep.cwd or len(self._task_history_cwd.items)>1 and self._task_history_cwd.items[-1]==self._task_history_cwd.items[-2]:
                        # if command same as previous, don't add (collapse history)
                        self._task_history_cwd.items[-1] = ''
                    else:
                        self._task_history_cwd.items.append('')   # new command about to be edited
                # submitting clears (which sets pos to end of history)
                do_clear = True
            if disabled:
                utils.pop_disabled()
                if disabled1:
                    if self._task_prep.type==task.Type.Wake_on_LAN:
                        reason = 'Select offline computer(s) to start up'
                    else:
                        reason = 'Select running computer(s) on which to execute this task'
                utils.draw_hover_text(reason if disabled1 else 'Provide task parameters', text='', hovered_flags=imgui.HoveredFlags_.allow_when_disabled)
            imgui.same_line(imgui.get_content_region_avail().x-imgui.calc_text_size('Clear').x-2*imgui.get_style().frame_padding.x)
            if imgui.button('Clear'):
                do_clear = True
            if do_clear:
                self._set_task_prep(task.TaskDef())
        imgui.end()

    def _imaging_GUI(self):
        dock_space_id = imgui.get_id("ImagingDockSpace")
        if not imgui.internal.dock_builder_get_node(dock_space_id):
            # first time this GUI is shown, set up as follows:
            #    ____________________________________
            #    |        |          Image          |
            #    | Image  |         details         |
            #    |  List  |-------------------------|
            #    |        |      Image actions      |
            #    ------------------------------------
            imgui.internal.dock_builder_remove_node(dock_space_id)
            imgui.internal.dock_builder_add_node(dock_space_id)

            imaging_GUI_list_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(dock_space_id, imgui.Dir_.left,0.20)
            imaging_GUI_details_dock_id,_,imaging_GUI_action_dock_id = imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.up,0.8)
            # make sure you can't dock in these
            node = imgui.internal.dock_builder_get_node(imaging_GUI_list_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(imaging_GUI_details_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(imaging_GUI_action_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)

            imgui.internal.dock_builder_dock_window('images_list_pane', imaging_GUI_list_dock_id)
            imgui.internal.dock_builder_dock_window('image_details_pane', imaging_GUI_details_dock_id)
            imgui.internal.dock_builder_dock_window('imaging_actions_pane', imaging_GUI_action_dock_id)
            imgui.internal.dock_builder_finish(dock_space_id)
        imgui.dock_space(dock_space_id, (0.,0.), imgui.DockNodeFlags_.no_docking_split|imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)

        if imgui.begin('images_list_pane'):
            imgui.text('Basis images:')
            for im in self._images_list:
                if not im['PartOfProject'] and imgui.button(im['UserFacingName']):
                    self._selected_image_id = im['Id']

            imgui.separator()
            imgui.text('Project images:')
            if imgui.button('+ new image'):
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
                    return 0 if imgui.is_key_released(imgui.Key.enter) else None

                buttons = {
                    icons_fontawesome.ICON_FA_CHECK+" Add image": lambda: async_thread.run(self.master.toems_create_disk_image(new_image_name),
                                                                        lambda fut: self._image_action_result('create',fut)),
                    icons_fontawesome.ICON_FA_BAN+" Cancel": None
                }
                utils.push_popup(self, lambda: utils.popup("Add image", _add_image_popup, buttons = buttons, closable=True))

            for im in self._images_list:
                if im['PartOfProject'] and imgui.button(im['UserFacingName']):
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
                        self._active_imaging_tasks_updater = async_thread.run(self.update_running_image_tasks(), self._restart_active_imaging_tasks_updater)
                    if im['Id'] not in self._image_description_cache:
                        self._image_description_cache[im['Id']] = [False, im['Description']]
                    if imgui.begin_table("##image_infos",2):
                        imgui.table_setup_column("##image_infos_left", imgui.TableColumnFlags_.width_fixed)
                        imgui.table_setup_column("##image_infos_right", imgui.TableColumnFlags_.width_stretch)
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        imgui.text("Image name")
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        imgui.text(im['UserFacingName'])
                        if im['PartOfProject']:
                            imgui.same_line()
                            if imgui.button(icons_fontawesome.ICON_FA_EDIT):
                                new_image_name = im['UserFacingName']
                                def _rename_image_popup():
                                    nonlocal new_image_name
                                    imgui.dummy((30*imgui.calc_text_size('x').x,0))
                                    if imgui.begin_table("##edit_image",2):
                                        imgui.table_setup_column("##edit_image_left", imgui.TableColumnFlags_.width_fixed)
                                        imgui.table_setup_column("##edit_image_right", imgui.TableColumnFlags_.width_stretch)
                                        imgui.table_next_row()
                                        imgui.table_next_column()
                                        imgui.align_text_to_frame_padding()
                                        imgui.text("Image name")
                                        imgui.table_next_column()
                                        imgui.set_next_item_width(-1)
                                        _,new_image_name = imgui.input_text("##edited_image_name",new_image_name)
                                        imgui.end_table()
                                    return 0 if imgui.is_key_released(imgui.Key.enter) else None

                                buttons = {
                                    icons_fontawesome.ICON_FA_CHECK+" Rename image": lambda: async_thread.run(self.master.toems_update_disk_image(im['Name'],{'Name':self.master.project + '_' + new_image_name}),
                                                                                        lambda fut: self._image_action_result('update',fut)),
                                    icons_fontawesome.ICON_FA_BAN+" Cancel": None
                                }
                                utils.push_popup(self, lambda: utils.popup("Rename image", _rename_image_popup, buttons = buttons, closable=True))
                            utils.draw_hover_text('Edit image name','')
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text("Description")
                        imgui.table_next_column()
                        if disabled := not im['PartOfProject']:
                            utils.push_disabled()
                        changed,self._image_description_cache[im['Id']][1] = imgui.input_text_multiline(f"##image{im['Id']}_description",self._image_description_cache[im['Id']][1],flags=imgui.InputTextFlags_.allow_tab_input)
                        if changed:
                            self._image_description_cache[im['Id']][0] = self._image_description_cache[im['Id']][1]!=im['Description']
                        if disabled:
                            utils.pop_disabled()
                        do_update = False
                        if self._image_description_cache[im['Id']][0]:
                            imgui.same_line()
                            imgui.begin_group()
                            do_update = imgui.button('Save')
                            if imgui.button('Discard changes'):
                                self._image_description_cache[im['Id']] = [False, im['Description']]
                            imgui.end_group()
                        if do_update:
                            async_thread.run(self.master.toems_update_disk_image(im['Name'],{'Description': self._image_description_cache[im['Id']][1]}),
                                             lambda fut: self._image_action_result('update',fut))
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text("Size on disk")
                        imgui.table_next_column()
                        imgui.text(im['DiskSize'])
                        if im['DiskSize']!='N/A':
                            imgui.table_next_column()
                            imgui.text("Upload time")
                            imgui.table_next_column()
                            imgui.text(im['TimeStamp'].replace('T',' '))
                            imgui.table_next_column()
                            imgui.text("Source computer")
                            imgui.table_next_column()
                            imgui.text(im['SourceComputer'])
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
                                if 'upload' in t['Type']:
                                    self._active_upload_tasks.add(t['ComputerName'])
                                    self._active_upload_tasks_map[t['ComputerName']] = self._selected_image_id
                                imgui.table_next_row()
                                imgui.table_next_column()
                                imgui.align_text_to_frame_padding()
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
                                    async_thread.run(self.master.toems_cancel_active_imaging_task(t['TaskId']),
                                                     lambda fut: self._image_action_result('cancel active task',fut))
                            imgui.end_table()
                        if imgui.button('Cancel all'):
                            # NB: cannot use the /ActiveImagingTask/CancelAllImagingTasks toems action, thats only for admins
                            # so issue cancels one by one
                            for t in active_imaging_tasks:
                                async_thread.run(self.master.toems_cancel_active_imaging_task(t['TaskId']),
                                                 lambda fut: self._image_action_result('cancel active task',fut))

        imgui.end()
        if imgui.begin('imaging_actions_pane'):
            if im:
                selected_clients = [id for id in self.selected_computers if self.selected_computers[id]]
                if (disabled := not selected_clients or im['DiskSize']=='N/A'):
                    utils.push_disabled()
                if imgui.button('Deploy'):
                    async_thread.run(self.master.toems_deploy_disk_image(im['Name'], im['PartOfProject'], selected_clients),
                                     lambda fut: self._image_action_result('deploy',fut))
                if not disabled and imgui.is_item_hovered():
                    stations_txt = '\n  '.join((self.master.clients[i].name for i in selected_clients))
                    utils.draw_tooltip(f"Deploy image '{im['UserFacingName']}' to selected stations:\n  "+stations_txt)
                if disabled:
                    utils.pop_disabled()
                    if im['DiskSize']=='N/A':
                        utils.draw_hover_text('Cannot deploy empty image', text='', hovered_flags=imgui.HoveredFlags_.allow_when_disabled)

                if im['PartOfProject']:
                    if selected_clients:
                        station_txt = next((self.master.clients[i].name for i in selected_clients))
                    if (disabled := len(selected_clients)!=1 or station_txt in self._active_upload_tasks):
                        utils.push_disabled()
                    if imgui.button('Upload'):
                        async_thread.run(self.master.toems_upload_to_disk_image(selected_clients[0], im['Name']),
                                         lambda fut: self._image_action_result('upload',fut))
                    if not disabled and imgui.is_item_hovered():
                        utils.draw_tooltip(f"Upload station {station_txt} to image '{im['UserFacingName']}'")
                    if disabled:
                        utils.pop_disabled()
                        if self._selected_image_id in self._active_upload_tasks:
                            utils.draw_hover_text('Cannot upload image, upload task is already running', text='', hovered_flags=imgui.HoveredFlags_.allow_when_disabled)

                    imgui.push_style_color(imgui.Col_.button, imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.43)))
                    imgui.push_style_color(imgui.Col_.button_hovered, imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.64)))
                    imgui.push_style_color(imgui.Col_.button_active, imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.93)))
                    if imgui.button('Delete'):
                        async_thread.run(self.master.toems_delete_disk_image(im['Name']),
                                        lambda fut: self._image_action_result('delete',fut))
                    imgui.pop_style_color(3)
        imgui.end()

    def _file_GUI(self):
        with self.master.clients_lock:
            selected_clients = [cid for cid in self.selected_computers if self.selected_computers[cid] and cid in self.master.clients and self.master.clients[cid].online]
        if disabled := not selected_clients:
            utils.push_disabled()
        if imgui.button('Start new action'):
            if not self._file_commander:
                file_action_provider_args = {'network': config.master['network'], 'master': self.master}
                self._file_commander = file_commander.FileCommander(mainGUI=self, master=self.master, selected_clients=self.selected_computers, file_action_provider_args=file_action_provider_args, title="Start copy action")
                self._file_commander.win_name = f'{self._file_commander.title}##file_commander'
            if win := hello_imgui.get_runner_params().docking_params.dockable_window_of_name(self._file_commander.win_name):
                win.focus_window_at_next_frame = True
                self._file_commander._is_visible = True
            else:
                window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
                new_window = hello_imgui.DockableWindow()
                new_window.label = self._file_commander.win_name
                new_window.gui_function = self._file_commander.draw
                new_window.can_be_closed = True
                new_window.imgui_window_flags = imgui.WindowFlags_.no_collapse
                new_window.window_size = self._file_commander.get_desired_size()
                new_window.window_size_condition = imgui.Cond_.appearing
                window_list.append(new_window)
                self._window_list = window_list
                self._to_focus = self._file_commander.win_name
        if disabled:
            utils.pop_disabled()
            utils.draw_hover_text('You should select at least one running client to perform the actions on', text='', hovered_flags=imgui.HoveredFlags_.allow_when_disabled)
        imgui.text('Task overview:')
        imgui.begin_child("##file_actions")
        table_flags = (
                imgui.TableFlags_.scroll_x |
                imgui.TableFlags_.scroll_y |
                imgui.TableFlags_.hideable |
                imgui.TableFlags_.sortable |
                imgui.TableFlags_.sort_multi |
                imgui.TableFlags_.reorderable |
                imgui.TableFlags_.sizing_fixed_fit |
                imgui.TableFlags_.no_host_extend_y
            )
        if imgui.begin_table(f"##file_action_list",column=6,flags=table_flags):
            imgui.table_setup_column("ID", imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 0
            imgui.table_setup_column("Client", imgui.TableColumnFlags_.no_hide)  # 1
            imgui.table_setup_column("Status", imgui.TableColumnFlags_.no_hide)  # 2
            imgui.table_setup_column("Action", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.no_hide)  # 3
            imgui.table_setup_column("Path")  # 4
            imgui.table_setup_column("Path 2")  # 5
            imgui.table_setup_scroll_freeze(0, 1)  # Sticky column headers

            # Headers
            imgui.table_next_row(imgui.TableRowFlags_.headers)
            for i in range(6):
                imgui.table_set_column_index(i)
                imgui.table_header(imgui.table_get_column_name(i))

            # gather all file actions
            actions = []
            with self.master.clients_lock:
                for c in self.master.clients:
                    if not self.master.clients[c].online:
                        continue
                    for a in self.master.clients[c].online.file_actions:
                        action = self.master.clients[c].online.file_actions[a]
                        # get action str
                        action_str = ''
                        match action["action"]:
                            case message.Message.FILE_MAKE:
                                action_str = 'Make folder' if action["is_dir"] else 'Make file'
                            case message.Message.FILE_RENAME:
                                action_str = 'Rename'
                            case message.Message.FILE_COPY_MOVE:
                                action_str = 'Move' if action["is_move"] else 'Copy'
                            case message.Message.FILE_DELETE:
                                action_str = 'Delete'
                        # get path
                        path = None
                        if 'path' in action:
                            path = action['path']
                        elif 'old_path' in action:
                            path = action['old_path']
                        elif 'source_path' in action:
                            path = action['source_path']
                        # get second path
                        path2 = None
                        if 'new_path' in action:
                            path2 = action['new_path']
                        elif 'dest_path' in action:
                            path2 = action['dest_path']
                        actions.append([a, self.master.clients[c].name, action, action_str, path, path2])

            # sort
            sort_specs = imgui.table_get_sort_specs()
            sort_specs = [sort_specs.get_specs(i) for i in range(sort_specs.specs_count)]
            idxs = list(range(len(actions)))
            for sort_spec in reversed(sort_specs):
                match sort_spec.column_index:
                    case 0:     # action ID
                        key = lambda idx: actions[idx][0]
                    case 1:     # client name
                        key = lambda idx: actions[idx][1]
                    case 2:     # status
                        key = lambda idx: actions[idx][2]["status"]
                    case 3:     # action
                        key = lambda idx: actions[idx][3]
                    case 4:     # path
                        key = natsort.os_sort_keygen(key=lambda idx: actions[idx][4])
                    case 5:     # path 2
                        key = natsort.os_sort_keygen(key=lambda idx: actions[idx][5])

                idxs.sort(key=key, reverse=bool(sort_spec.get_sort_direction() - 1))
            actions = [actions[i] for i in idxs]

            # render actions
            for action in actions:
                imgui.table_next_row()

                for ci in range(6):
                    if not (imgui.table_get_column_flags(ci) & imgui.TableColumnFlags_.is_enabled):
                        continue
                    imgui.table_set_column_index(ci)

                    match ci:
                        case 0:
                            # ID
                            imgui.text(f'{action[0]}')
                        case 1:
                            # Client
                            imgui.text(action[1])
                        case 2:
                            # Status
                            match action[2]["status"]:
                                case structs.Status.Pending:
                                    imgui.text_colored((.5,.5,.5,1.),icons_fontawesome.ICON_FA_HOURGLASS)
                                case structs.Status.Running:
                                    symbol_size = imgui.calc_text_size("x").y/2
                                    spinner_radii = [x/22*symbol_size for x in [22, 16, 10]]
                                    lw = 3.5/22*symbol_size
                                    imspinner.spinner_ang_triple(f'loadingSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
                                case structs.Status.Finished:
                                    imgui.text_colored((.0,1.,0.,1.),icons_fontawesome.ICON_FA_CHECK)
                                case structs.Status.Errored:
                                    imgui.text_colored((1.,.0,.0,1.),icons_fontawesome.ICON_FA_EXCLAMATION_TRIANGLE)
                                    if imgui.is_item_hovered():
                                        utils.draw_tooltip(f'Error: {utils.trim_str(str(action[2]["error"]),1000)}')
                        case 3:
                            # Action
                            imgui.text(f'{action[3]}')
                        case 4:
                            # Path
                            if action[4]:
                                imgui.text(f'{action[4]}')
                        case 5:
                            # Path 2
                            if action[5]:
                                imgui.text(f'{action[5]}')

            imgui.end_table()
        imgui.end_child()


    async def update_running_image_tasks(self):
        self._active_imaging_tasks_updater_should_stop = False
        while not self._active_imaging_tasks_updater_should_stop:
            # NB: this is and must remain an atomic update, so its not possible to read incomplete state elsewhere
            self._active_imaging_tasks = await self.master.toems_get_active_imaging_tasks()

            # check if an upload task has just finished. If so, trigger image refresh
            to_del = []
            for c in self._active_upload_tasks:
                found = False
                for t in self._active_imaging_tasks:
                    if t['ComputerName']==c:
                        found = True
                        break
                if not found:
                    # computer no longer has an active task, assume upload is finished
                    # trigger image refresh
                    to_del.append(c)
                    await self._get_project_images()
            for c in to_del:
                self._active_upload_tasks.remove(c)
                del self._active_upload_tasks_map[c]

            # sleep until the next whole second
            now = time.time()
            await asyncio.sleep(math.ceil(now) - now)
        self._active_imaging_tasks_updater = None
    def _restart_active_imaging_tasks_updater(self, future: asyncio.Future):
        try:
            future.exception()
        except concurrent.futures.CancelledError:
            pass
        self._active_imaging_tasks_updater = None
        if not self._active_imaging_tasks_updater_should_stop:
            self._active_imaging_tasks_updater = async_thread.run(self.update_running_image_tasks(), self._restart_active_imaging_tasks_updater)

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
            if action in ['create','update','deploy','upload','delete']:
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


    def _open_computer_detail(self, item: structs.Client):
        win_name = f'{item.name}##computer_view'
        if win := hello_imgui.get_runner_params().docking_params.dockable_window_of_name(win_name):
            win.focus_window_at_next_frame = True
        else:
            window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
            window_list.append(
                self._make_main_space_window(win_name, lambda: self._computer_detail_GUI(item), can_be_closed=True)
            )
            self._window_list = window_list
            self._to_dock = [win_name]
            self._to_focus= win_name
            self._computer_GUI_tasks[item.id] = None

    def _computer_detail_GUI(self, item: structs.Client):
        if not item.online:
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

            computer_GUI_list_dock_id,_,temp_id = imgui.internal.dock_builder_split_node_py(dock_space_id, imgui.Dir_.left,0.15)
            computer_GUI_details_dock_id,_,computer_GUI_action_dock_id = imgui.internal.dock_builder_split_node_py(temp_id, imgui.Dir_.up,0.15)
            # make sure you can't dock in these
            node = imgui.internal.dock_builder_get_node(computer_GUI_list_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(computer_GUI_details_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)
            node = imgui.internal.dock_builder_get_node(computer_GUI_action_dock_id)
            node.set_local_flags(node.local_flags | imgui.internal.DockNodeFlagsPrivate_.no_docking)

            imgui.internal.dock_builder_dock_window(f'task_list_pane_{item.id}', computer_GUI_list_dock_id)
            imgui.internal.dock_builder_dock_window(f'task_result_pane_{item.id}', computer_GUI_details_dock_id)
            imgui.internal.dock_builder_dock_window(f'task_log_pane_{item.id}', computer_GUI_action_dock_id)
            imgui.internal.dock_builder_finish(dock_space_id)
        imgui.dock_space(dock_space_id, (0.,0.), imgui.DockNodeFlags_.no_docking_split|imgui.internal.DockNodeFlagsPrivate_.no_tab_bar)

        if imgui.begin(f'task_list_pane_{item.id}'):
            if item.online:
                nchar = int((imgui.get_content_region_max().x)//imgui.calc_text_size('x').x)-1
                if item.online.tasks:
                    show = True
                    if item.online.et_events:
                        show = imgui.collapsing_header('Tasks:')
                    else:
                        imgui.text('Tasks:')
                    if show:
                        imgui.push_font(imgui_md.get_code_font())
                        for id in item.online.tasks:
                            tsk = item.online.tasks[id]
                            if tsk.type==task.Type.Wake_on_LAN:
                                lbl = tsk.type.value
                                hover_text = tsk.type.value
                            else:
                                lbl = utils.trim_str(tsk.payload, length=nchar, newline_ellipsis=True)
                                hover_text = tsk.type.value+':\n'+tsk.payload
                            # decide button color
                            match tsk.status:
                                case structs.Status.Pending:
                                    clr = (.5,.5,.5,1.)
                                case structs.Status.Running:
                                    clr = (1.,1.,0.,1.)
                                case structs.Status.Finished:
                                    clr = (.0,1.,0.,1.)
                                case structs.Status.Errored:
                                    clr = (1.,.0,.0,1.)
                            alpha = 0.3
                            imgui.push_style_color(imgui.Col_.button,[x*alpha+y*(1-alpha) for x,y in zip(clr,imgui.get_style_color_vec4(imgui.Col_.button))])
                            imgui.push_style_color(imgui.Col_.button_hovered,[x*alpha+y*(1-alpha) for x,y in zip(clr,imgui.get_style_color_vec4(imgui.Col_.button_hovered))])
                            imgui.push_style_color(imgui.Col_.button_active,[x*alpha+y*(1-alpha) for x,y in zip(clr,imgui.get_style_color_vec4(imgui.Col_.button_active))])
                            if imgui.button(f'{lbl}##{tsk.id}'):
                                self._computer_GUI_tasks[item.id] = ['task',id,0]
                            imgui.pop_style_color(3)
                            if imgui.begin_popup_context_item(f'##{lbl}_{tsk.id}_context'):
                                if imgui.selectable(f"Copy task##selectable", False)[0]:
                                    self._set_task_prep(tsk)
                                    self._to_focus = 'Tasks'
                                if tsk.status in [structs.Status.Pending, structs.Status.Running]:
                                    if tsk.status==structs.Status.Pending:
                                        action_txt = 'Cancel'
                                    else:
                                        if tsk.interactive and (item.id, id) not in self._computer_GUI_interactive_sent_finish:
                                            action_txt = 'Finish'
                                        else:
                                            action_txt = 'Stop'
                                    if imgui.selectable(f'{action_txt}##{id}', False)[0]:
                                        async_thread.run(task.send_cancel(item,tsk))
                                        if tsk.interactive:
                                            self._computer_GUI_interactive_sent_finish[(item.id, id)] = True
                                imgui.end_popup()
                            elif imgui.is_item_hovered():
                                # show no more than 10 lines
                                lines = hover_text.splitlines()
                                to_show = '\n'.join(lines[0:10])
                                if len(lines)>10:
                                    to_show += '\n...'
                                utils.draw_tooltip(to_show)
                        imgui.pop_font()
                if item.online.et_events:
                    show = True
                    if item.online.tasks:
                        show = imgui.collapsing_header('Eye-tracker events:')
                    else:
                        imgui.text('Eye-tracker events:')
                    if show:
                        for i,evt in enumerate(item.online.et_events):
                            str,full_info,_ = eye_tracker.format_event(evt)
                            lbl = utils.trim_str(str, length=nchar, newline_ellipsis=True)
                            if imgui.button(f'{lbl}##et_{i}'):
                                self._computer_GUI_tasks[item.id] = ['ET',i,0]
                            utils.draw_hover_text(hover_text=full_info,text='')
                if not item.online.tasks and not item.online.et_events:
                    imgui.text_wrapped('no tasks or eye tracker events available')
        imgui.end()
        if imgui.begin(f'task_result_pane_{item.id}'):
            if item.online and (tid := self._computer_GUI_tasks[item.id]) is not None:
                if tid[0]=='task':
                    tsk = item.online.tasks[tid[1]]
                    width = imgui.get_content_region_avail().x
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
                            line_height = imgui.get_font_size()
                            num_visible_lines = 10
                            editor_size = imgui.ImVec2(width, line_height*(num_visible_lines+1))
                            self._task_GUI_editor.render("Code", False, editor_size)
                            imgui.pop_font()
                        else:
                            utils.push_disabled()
                            imgui.set_next_item_width(width-imgui.calc_text_size(icons_fontawesome.ICON_FA_COPY+' ').x-2*imgui.get_style().frame_padding.x-imgui.get_style().item_spacing.x)
                            imgui.input_text(f'##task_payload', tsk.payload)
                            utils.pop_disabled()
                            imgui.pop_font()
                            imgui.same_line()
                            if imgui.button(icons_fontawesome.ICON_FA_COPY+'##command'):
                                self._computer_GUI_command_copy_t = immapp.clock_seconds()
                                imgui.set_clipboard_text(tsk.payload)
                            if imgui.is_item_hovered():
                                was_copied_recently = self._computer_GUI_command_copy_t is not None and (immapp.clock_seconds()-self._computer_GUI_command_copy_t) < 0.7
                                if was_copied_recently:
                                    imgui.set_tooltip("Copied!")
                                else:
                                    imgui.set_tooltip("Copy")
                    if tsk.cwd:
                        imgui.text('cwd')
                        imgui.push_font(imgui_md.get_code_font())
                        utils.push_disabled()
                        imgui.set_next_item_width(width-imgui.calc_text_size(icons_fontawesome.ICON_FA_COPY+' ').x-2*imgui.get_style().frame_padding.x-imgui.get_style().item_spacing.x)
                        imgui.input_text(f'##task_cwd', tsk.cwd)
                        utils.pop_disabled()
                        imgui.pop_font()
                        imgui.same_line()
                        if imgui.button(icons_fontawesome.ICON_FA_COPY+'##cwd'):
                            self._computer_GUI_cwd_copy_t = immapp.clock_seconds()
                            imgui.set_clipboard_text(tsk.cwd)
                        if imgui.is_item_hovered():
                            was_copied_recently = self._computer_GUI_cwd_copy_t is not None and (immapp.clock_seconds()-self._computer_GUI_cwd_copy_t) < 0.7
                            if was_copied_recently:
                                imgui.set_tooltip("Copied!")
                            else:
                                imgui.set_tooltip("Copy")
                    imgui.align_text_to_frame_padding()
                    imgui.text(tsk.status.value)
                    if tsk.return_code is not None:
                        imgui.text(f'return code: {tsk.return_code}')
                    if tsk.status in [structs.Status.Pending, structs.Status.Running]:
                        imgui.same_line()
                        if tsk.status==structs.Status.Pending:
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
                    evt = item.online.et_events[tid[1]]
                    _,_,evt_info = eye_tracker.format_event(evt)
                    imgui.text(f'Timestamp: {evt_info[0]}')
                    imgui.text(f'Event: {evt_info[1]}')
                    if len(evt_info)>2:
                        imgui.text(f'Info: {evt_info[2]}')
            elif item.online:
                imgui.text('select a task or eye tracker event on the left')
        imgui.end()
        if imgui.begin(f'task_log_pane_{item.id}'):
            if item.online and (tid := self._computer_GUI_tasks[item.id]) is not None:
                if tid[0]=='task' and (tsk:=item.online.tasks[tid[1]]).type!=task.Type.Wake_on_LAN:
                    imgui.push_font(imgui_md.get_code_font())
                    imgui.input_text_multiline(f"##output_content", tsk.output, size=(imgui.get_content_region_avail().x,-imgui.get_frame_height_with_spacing()), flags=imgui.InputTextFlags_.read_only)
                    # scroll to bottom if output has changed
                    output_length = len(tsk.output)
                    if tid[2]!=output_length:
                        if tid[2]>0:
                            # need one frame delay for win.scroll_max.y to be updated
                            tid[2]=0
                        else:
                            win_name = imgui.get_current_context().current_window.name
                            win = imgui.internal.find_window_by_name(f'{win_name}/##output_content_{imgui.get_id("##output_content"):08X}') # https://github.com/ocornut/imgui/issues/5484#issuecomment-1189989347
                            imgui.internal.set_scroll_y(win, win.scroll_max.y)
                            tid[2] = output_length
                    imgui.pop_font()
                    # if interactive task, add text box for input
                    if tsk.interactive and tsk.status==structs.Status.Running and ((item.id, tid[1]) not in self._computer_GUI_interactive_sent_finish or not self._computer_GUI_interactive_sent_finish[(item.id, tid[1])]):
                        if (item.id, tid[1]) not in self._computer_GUI_interactive_tasks:
                            self._computer_GUI_interactive_tasks[(item.id, tid[1])] = ''
                        if (item.id, tid[1]) not in self._computer_GUI_interactive_history:
                            self._computer_GUI_interactive_history[(item.id, tid[1])] = History()

                        def handle_history(this: MainGUI, key: tuple[int, int], data: imgui.InputTextCallbackData):
                            hist = this._computer_GUI_interactive_history[key]
                            if data.event_flag==imgui.InputTextFlags_.callback_edit:
                                # store current state to item -1 (the item currently being edited)
                                # NB: this means that upon history navigation, current item is only replaced once a history item is edited
                                hist.items[-1] = data.buf
                                hist.pos = -1
                            elif data.event_flag==imgui.InputTextFlags_.callback_history:
                                # replace current buffer with history
                                new_hist_pos = hist.pos
                                if data.event_key==imgui.Key.up_arrow:
                                    if new_hist_pos == -1:
                                        new_hist_pos = len(hist.items)-1 - 1    # -1 for indexing, -1 for going one back in history
                                    elif new_hist_pos>0:
                                        new_hist_pos -= 1
                                elif data.event_key==imgui.Key.down_arrow:
                                    if new_hist_pos != -1:
                                        new_hist_pos += 1
                                        if new_hist_pos==len(hist.items)-1:
                                            new_hist_pos = -1
                                if new_hist_pos != hist.pos:
                                    hist.pos = new_hist_pos
                                    data.delete_chars(0, data.buf_text_len)
                                    data.insert_chars(0, hist.items[hist.pos])
                            return 0

                        imgui.set_next_item_width(width-imgui.calc_text_size("Send").x-2*imgui.get_style().frame_padding.x-2*imgui.get_style().item_spacing.x-imgui.get_frame_height_with_spacing())  # space for send button and for arrow button
                        enter_pressed, self._computer_GUI_interactive_tasks[(item.id, tid[1])] = \
                            imgui.input_text(f'##interactive_input{item.id},{tid[1]}', self._computer_GUI_interactive_tasks[(item.id, tid[1])], flags=imgui.InputTextFlags_.enter_returns_true|imgui.InputTextFlags_.escape_clears_all|imgui.InputTextFlags_.callback_history|imgui.InputTextFlags_.callback_edit, callback=lambda x: handle_history(self, (item.id, tid[1]), x))
                        if enter_pressed:
                            imgui.set_keyboard_focus_here(-1)   # refocus above input_text box
                        imgui.same_line()
                        disabled = len(self._computer_GUI_interactive_history[(item.id, tid[1])].items)<=1
                        if disabled:
                            utils.push_disabled()
                        button_pos = imgui.get_cursor_screen_pos()
                        if imgui.arrow_button('##interactive_history_button', imgui.Dir_.down):
                            imgui.set_next_window_pos([x+y for x,y in zip(button_pos,(0,imgui.get_frame_height_with_spacing()))])
                            imgui.open_popup('##interactive_history_popup')
                        if imgui.begin_popup('##interactive_history_popup'):
                            idx = self._computer_GUI_interactive_history[(item.id, tid[1])].pos
                            if idx==-1:
                                idx = len(self._computer_GUI_interactive_history[(item.id, tid[1])].items)-1
                            changed, idx = imgui.list_box('##interactive_history_popup_select',idx,self._computer_GUI_interactive_history[(item.id, tid[1])].items)
                            if changed:
                                if idx==len(self._computer_GUI_interactive_history[(item.id, tid[1])].items)-1:
                                    self._computer_GUI_interactive_history[(item.id, tid[1])].pos = -1
                                else:
                                    self._computer_GUI_interactive_history[(item.id, tid[1])].pos = idx
                                self._computer_GUI_interactive_tasks[(item.id, tid[1])] = self._computer_GUI_interactive_history[(item.id, tid[1])].items[idx]
                                imgui.close_current_popup()
                            imgui.end_popup()
                        if disabled:
                            utils.pop_disabled()
                        imgui.same_line()
                        if imgui.button(f'Send##{item.id},{tid[1]}') or enter_pressed:
                            # send
                            async_thread.run(task.send_input(self._computer_GUI_interactive_tasks[(item.id, tid[1])]+'\n',item,tsk))
                            # deal with history
                            hist = self._computer_GUI_interactive_history[(item.id, tid[1])]
                            hist.items[-1] = self._computer_GUI_interactive_tasks[(item.id, tid[1])]    # should be equal, but lets be sure
                            if len(hist.items)>1 and hist.items[-1]==hist.items[-2]:
                                # if command same as previous, don't add (collapse history)
                                hist.items[-1] = ''
                            else:
                                hist.items.append('')   # new command about to be edited
                            hist.pos = -1   # submitting resets pos to end of history
                            # done
                            self._computer_GUI_interactive_tasks[(item.id, tid[1])] = ''
        imgui.end()


    def _computer_list_pane(self):
        # this pane is always visible, so we handle popups here
        utils.handle_popup_stack(self.popup_stack)
        # also handle showing of debug windows
        if self.show_demo_window:
            self.show_demo_window = imgui.show_demo_window(self.show_demo_window)

        # now render actual pane
        if self.proj_select_state!=structs.Status.Finished and not self.no_login_mode:
            return

        imgui.align_text_to_frame_padding()
        imgui.text('Select:')
        imgui.same_line()
        with self.master.clients_lock:
            if imgui.button('On'):
                utils.set_all(self.selected_computers, False)
                utils.set_all(self.selected_computers, True, predicate=lambda id: self.master.clients[id].online)
            utils.draw_hover_text('Select all running computers',text='')
            imgui.same_line()
            if imgui.button('Off'):
                utils.set_all(self.selected_computers, False)
                utils.set_all(self.selected_computers, True, predicate=lambda id: not self.master.clients[id].online)
            utils.draw_hover_text('Select all computers that are shut down',text='')
            imgui.same_line()
            if imgui.button('Invert'):
                new_vals = {k: not self.selected_computers[k] for k in self.selected_computers}
                self.selected_computers.clear()
                self.selected_computers |= new_vals
            utils.draw_hover_text('Invert selection of computers',text='')

            if len(self.selected_computers)!=len(self.master.clients):
                # update: remove from or add to selected as needed
                # NB: slightly complicated as we cannot replace the dict. A ref to it is
                # held by self.computer_lister, and that reffed object needs to be updated
                new_vals = {k:(self.selected_computers[k] if k in self.selected_computers else False) for k in self.master.clients}
                self.selected_computers.clear()
                self.selected_computers |= new_vals

        imgui.begin_child("##computer_list_frame", size=(0,-imgui.get_frame_height_with_spacing()), window_flags=imgui.WindowFlags_.horizontal_scrollbar)
        self.computer_lister.draw()
        imgui.end_child()