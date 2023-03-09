from enum import Enum, auto
import numpy as np
import asyncio
import concurrent
import json

from imgui_bundle import hello_imgui, icons_fontawesome, imgui, immapp, imspinner, imgui_md
from imgui_bundle.demos_python import demo_utils

from ...utils import async_thread, config, network, structs
from .. import Master
from ._impl import msgbox, utils

# Struct that holds the application's state
class ActionState(Enum):
    Not_Done    = auto()
    Processing  = auto()
    Done        = auto()

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
        self.project          = ''

        self._window_list     = []

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
        runner_params.app_window_params.window_title = "labManager Master"

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

        # Finally, transmit these splits to HelloImGui
        runner_params.docking_params.docking_splits = [split_main_left]

        # 2.2 Define our dockable windows : each window provide a Gui callback, and will be displayed
        #     in a docking split.

        # Computer list on the left
        self.computer_list = hello_imgui.DockableWindow()
        self.computer_list.label = "Computers"
        self.computer_list.dock_space_name = "LeftSpace"
        self.computer_list.gui_function = self._computer_list
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
        if self._window_list:
            hello_imgui.get_runner_params().docking_params.dockable_windows = self._window_list
            self._window_list = []

    def _make_main_space_window(self, name, gui_func):
        login_view = hello_imgui.DockableWindow()
        login_view.label = name
        login_view.dock_space_name = "MainDockSpace"
        login_view.gui_function = gui_func
        return login_view

    def _show_app_menu_items(self):
        do_close , _ = imgui.menu_item("Close project", "", False)
        do_logout, _ = imgui.menu_item("Log out", "", False)

        if do_logout:
            self._logout()
        elif do_close:
            self._unload_project()

    def _login_done(self):
        self.login_state = ActionState.Not_Done

    def _logout(self):
        self._unload_project()

        self.username         = ''
        self.password         = ''
        self.login_state      = ActionState.Not_Done
        self.master.logout()

    def _project_selected(self):
        self.proj_select_state = ActionState.Done
        # update GUI
        self.project = self.master.projects[self.proj_idx]
        self._window_list = [
            self.computer_list,
            self._make_main_space_window("Tasks", self._task_GUI),
            self._make_main_space_window("Image Management", self._imaging_GUI),
            ]
        # start server
        if_ips,_ = network.ifs.get_ifaces(config.master['network'])
        async_thread.run(self.master.start_server((if_ips[0], 0)))

    def _unload_project(self):
        if self.master.is_serving():
            async_thread.wait(self.master.stop_server())
        self.proj_select_state= ActionState.Not_Done
        self.proj_idx         = -1
        self.project          = ''
        self.master.unset_project()

        self._window_list = [self.computer_list, self._make_main_space_window("Login", self._login_GUI)]

    def _login_GUI(self):
        if self.login_state != ActionState.Done:
            disabled = self.login_state==ActionState.Processing
            if disabled:
                utils.push_disabled()
            i1,self.username = imgui.input_text('User name',self.username, flags=imgui.InputTextFlags_.enter_returns_true)
            i2,self.password = imgui.input_text('Password' ,self.password, flags=imgui.InputTextFlags_.enter_returns_true|imgui.InputTextFlags_.password)

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
                        async_thread.run(self.master.login(self.username,self.password), lambda fut: self._login_result('login',fut))

            if disabled:
                utils.pop_disabled()
        else:
            disabled = self.proj_select_state==ActionState.Processing
            if disabled:
                utils.push_disabled()
            imgui.text('Select project:')
            _,self.proj_idx = imgui.list_box('##Project', 0 if self.proj_idx==-1 else self.proj_idx, self.master.projects)

            if self.proj_select_state==ActionState.Processing:
                symbol_size = imgui.calc_text_size("x").y*2
                spinner_radii = [x/22/2*symbol_size for x in [22, 16, 10]]
                lw = 3.5/22/2*symbol_size
                imspinner.spinner_ang_triple(f'projSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
            else:
                if imgui.button("Select"):
                    self.proj_select_state = ActionState.Processing
                    async_thread.run(self.master.set_project(self.master.projects[self.proj_idx]), lambda fut: self._login_result('project',fut))

            if disabled:
                utils.pop_disabled()

    def _task_GUI(self):
        pass

    def _imaging_GUI(self):
        pass

    def _login_result(self, stage, future: asyncio.Future):
        try:
            exc = future.exception()
        except concurrent.futures.CancelledError:
            return
        if not exc:
            # log in successful
            if stage=='login':
                self.login_state = ActionState.Done
            elif stage=='project':
                self._project_selected()
            return

        # error occurred
        if stage=='login':
            self._login_done()
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

        # not handled by above, display more generic error
        tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
        utils.push_popup(self, msgbox.msgbox, "Login error", f"Something went wrong when {'logging in' if stage=='login' else 'selecting project'}...", msgbox.MsgBox.error, more=tb)

    def _computer_list(self):
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

        imgui_md.render_unindented(
            f"""
            ### User: {self.username}
            ### Project: {self.project}
            """
        )
        imgui.new_line()

        with self.master.known_clients_lock:
            for i in self.master.known_clients:
                _computer(self.master.known_clients[i])


def _computer(client: structs.KnownClient):
    label   = client.name

    is_online = client.client is not None
    prepend = icons_fontawesome.ICON_FA_EYE
    clr_off = (1., 0.2824, 0.2824, 1.)
    clr_on  = (0.0588, 0.4510, 0.0471, 1.)
    clrs = []
    if is_online:
        prepend += icons_fontawesome.ICON_FA_PLAY
        if client.client.eye_tracker:
            clrs.append(clr_on)
        else:
            clrs.append(clr_off)
        clrs.append(clr_on)
    else:
        prepend += icons_fontawesome.ICON_FA_POWER_OFF
        clrs.append(clr_off)
        clrs.append(clr_off)

    g = imgui.get_current_context()
    window = g.current_window
    id = window.get_id(f"{label}##Button")
    style = imgui.get_style()

    label_size = imgui.calc_text_size(prepend+'  '+label)
    prep_size  = [imgui.calc_text_size(c+' ') for c in prepend]
    prep_sizex = [p.x for p in prep_size]
    prep_sizex = [0.]+prep_sizex
    prep_off   = [sum(prep_sizex[0:i+1]) for i in range(len(prep_sizex))]
    pos = window.dc.cursor_pos
    size = imgui.internal.calc_item_size((0,0), label_size.x + style.frame_padding.x * 2., label_size.y + style.frame_padding.y * 2.)

    bb = imgui.internal.ImRect(pos[0],pos[1], pos[0]+size[0],pos[1]+size[1])
    imgui.internal.item_size(size, style.frame_padding.y)
    if not imgui.internal.item_add(bb, id):
        return

    if (g.last_item_data.in_flags & imgui.internal.ItemFlags_.button_repeat):
        flags |= imgui.internal.ItemFlags_.button_repeat

    pressed, hovered, held = imgui.internal.button_behavior(bb, id, False, False)

    # Render
    col = imgui.Col_.button
    if held and hovered:
        col = imgui.Col_.button_active
    elif hovered:
        col = imgui.Col_.button_hovered
    col = imgui.get_color_u32(idx=col)
    imgui.internal.render_nav_highlight(bb, id)
    imgui.internal.render_frame(bb.min, bb.max, col, True, style.frame_rounding)

    # render text
    imgui.push_style_color(imgui.Col_.text, clrs[0])
    pos = (bb.min[0]+style.frame_padding.x            , bb.min[1]+style.frame_padding.y)
    imgui.internal.render_text_clipped(pos, (pos[0]+prep_off[0], bb.max[1]-style.frame_padding.y), prepend[0], None, prep_size[0], style.button_text_align, bb)
    imgui.pop_style_color()
    imgui.push_style_color(imgui.Col_.text, clrs[1])
    pos = (bb.min[0]+style.frame_padding.x+prep_off[1], bb.min[1]+style.frame_padding.y)
    imgui.internal.render_text_clipped(pos, (pos[0]+prep_off[1], bb.max[1]-style.frame_padding.y), prepend[1], None, prep_size[1], style.button_text_align, bb)
    imgui.pop_style_color()

    pos = (bb.min[0]+style.frame_padding.x+prep_off[2], bb.min[1]+style.frame_padding.y)
    imgui.internal.render_text_clipped(pos, (pos[0]+prep_off[2], bb.max[1]-style.frame_padding.y), label, None, label_size, style.button_text_align, bb)

    if client.client:
        info = f'{client.client.host}:{client.client.port}'
        if client.client.eye_tracker:
            info += f'\n{client.client.eye_tracker.model}@{client.client.eye_tracker.frequency}Hz ({client.client.eye_tracker.firmware_version}, {client.client.eye_tracker.serial})'
        draw_hover_text(info,text='')

def draw_tooltip(hover_text):
    imgui.begin_tooltip()
    imgui.push_text_wrap_pos(min(imgui.get_font_size() * 35, imgui.get_io().display_size.x))
    imgui.text_unformatted(hover_text)
    imgui.pop_text_wrap_pos()
    imgui.end_tooltip()

def draw_hover_text(hover_text: str, text="(?)", force=False, *args, **kwargs):
    if text:
        imgui.text_disabled(text, *args, **kwargs)
    if force or imgui.is_item_hovered():
        draw_tooltip(hover_text)
        return True
    return False

def _splitter(split_vertically, thickness, size1, size2, min_size1, min_size2, splitter_long_axis_size = -1.0):
    g = imgui.get_current_context()
    window = g.current_window
    id = window.get_id("##Splitter")
    cp = window.dc.cursor_pos
    off= imgui.ImVec2(size1, 0.) if split_vertically else imgui.ImVec2(0., size1)
    sz = imgui.internal.calc_item_size(imgui.ImVec2(thickness, splitter_long_axis_size) if split_vertically else imgui.ImVec2(splitter_long_axis_size, thickness), 0., 0.)
    bb = imgui.internal.ImRect(cp[0]+off[0],cp[1]+off[1], cp[0]+off[0]+sz[0],cp[1]+off[1]+sz[1])
    #return imgui.internal.splitter_behavior(bb, id, imgui.internal.Axis.x if split_vertically else imgui.internal.Axis.y, size1, size2, min_size1, min_size2, 4., 0.04)
    np_size1 = np.array([size1],dtype='float32')
    np_size2 = np.array([size2],dtype='float32')
    retval = imgui.internal.splitter_behavior(bb, id, imgui.internal.Axis.x if split_vertically else imgui.internal.Axis.y, np_size1, np_size2, min_size1, 4., .04)#min_size2, 4., 0.04)
    return retval, np_size1[0], np_size2[0]