from enum import Enum
import numpy as np
import asyncio
import concurrent

from imgui_bundle import hello_imgui, icons_fontawesome, imgui, imgui_md, immapp
from imgui_bundle.demos_python import demo_utils

from ...utils import async_thread
from ._impl import msgbox, utils


# Struct that holds the application's state
class MainGUI:
    class RocketState(Enum):
        Init = 0
        Preparing = 1
        Launched = 2

    def __init__(self):
        # Constants
        self.sz1 = 300.
        self.sz2 = 300.

        self.f: float = 0.0
        self.counter: int = 0
        self.rocket_progress: float = 0.0

        self.rocket_state: MainGUI.RocketState = MainGUI.RocketState.Init

        self.popup_stack = []

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
                utils.push_popup(msgbox.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", msgbox.MsgBox.warn, more=tb)
                return
            utils.push_popup(msgbox.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", msgbox.MsgBox.error)
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
        # Important: HelloImGui uses an assets dir where it can find assets (fonts, images, etc.)
        #
        # By default an assets folder is installed via pip inside site-packages/lg_imgui_bundle/assets
        # and provides two fonts (fonts/DroidSans.ttf and fonts/fontawesome-webfont.ttf)
        # If you need to add more assets, make a copy of this assets folder and add your own files, and call set_assets_folder
        hello_imgui.set_assets_folder(demo_utils.demos_assets_folder())

        ################################################################################################
        # Part 1: Define the application state, fill the status and menu bars, and load additional font
        ################################################################################################

        # Hello ImGui params (they hold the settings as well as the Gui callbacks)
        runner_params = hello_imgui.RunnerParams()

        # Note: by setting the window title, we also set the name of the ini files into which the settings for the user
        # layout will be stored: Docking_demo.ini (imgui settings) and Docking_demo_appWindow.ini (app window size and position)
        runner_params.app_window_params.window_title = "labManager Master"

        runner_params.imgui_window_params.menu_app_title = "File"
        runner_params.app_window_params.window_geometry.size = (1400, 700)
        runner_params.app_window_params.restore_previous_geometry = True

        #
        # Status bar
        #
        runner_params.imgui_window_params.show_status_bar = True
        runner_params.imgui_window_params.show_status_fps = False
        runner_params.fps_idling.enable_idling = False
        runner_params.callbacks.load_additional_fonts = self._load_fonts
        runner_params.callbacks.show_status = self._status_bar_gui

        #
        # Menu bar
        #
        # We use the default menu of Hello ImGui, to which we add some more items
        runner_params.imgui_window_params.show_menu_bar = True

        def _show_app_menu_items():
            clicked, _ = imgui.menu_item("Log out", "", False)
            if clicked:
                hello_imgui.log(hello_imgui.LogLevel.info, "Logging out...")
            clicked, _ = imgui.menu_item("Close project", "", False)
            if clicked:
                hello_imgui.log(hello_imgui.LogLevel.info, "Closing project...")

        runner_params.callbacks.show_app_menu_items = _show_app_menu_items


        # optional native events handling
        # runner_params.callbacks.any_backend_event_callback = ...

        ################################################################################################
        # Part 2: Define the application layout and windows
        ################################################################################################

        #
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
        # In this demo, we also demonstrate multiple viewports.
        # you can drag windows outside out the main window in order to put their content into new native windows
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

        #
        # 2.1 Define our dockable windows : each window provide a Gui callback, and will be displayed
        #     in a docking split.
        #

        # A Command panel named "Commands" will be placed in "LeftSpace". Its Gui is provided calls "CommandGui"
        commands_window = hello_imgui.DockableWindow()
        commands_window.label = "Commands"
        commands_window.dock_space_name = "LeftSpace"
        commands_window.gui_function = self._command_gui
        # A Window named "Dear ImGui Demo" will be placed in "MainDockSpace"
        dear_imgui_demo_window = hello_imgui.DockableWindow()
        dear_imgui_demo_window.label = "Dear ImGui Demo"
        dear_imgui_demo_window.dock_space_name = "MainDockSpace"
        dear_imgui_demo_window.gui_function = imgui.show_demo_window

        # Finally, transmit these windows to HelloImGui
        runner_params.docking_params.dockable_windows = [
            commands_window,
            dear_imgui_demo_window,
        ]

        ################################################################################################
        # Part 3: Run the app
        ################################################################################################
        addons_params = immapp.AddOnsParams()
        addons_params.with_markdown = True
        immapp.run(runner_params, addons_params)

    # CommandGui: the widgets on the left panel
    def _command_gui(self):
        _,self.sz1,self.sz2 = splitter(False,2.,self.sz1,self.sz2,100.,100.)
        imgui.begin_child("basic",(-1,self.sz1))
        imgui_md.render_unindented(
            """
            # Basic widgets demo
            The widgets below will interact with the log window and the status bar.
            """
        )
        # Edit 1 float using a slider from 0.0f to 1.0f
        changed, self.f = imgui.slider_float("float", self.f, 0.0, 1.0)
        if changed:
            hello_imgui.log(hello_imgui.LogLevel.warning, f"state.f was changed to {self.f}")

        # Buttons return true when clicked (most widgets return true when edited/activated)
        if imgui.button("Button"):
            self.counter += 1
            hello_imgui.log(hello_imgui.LogLevel.info, "Button was pressed")

        imgui.same_line()
        imgui.text(f"counter = {self.counter}")

        if self.rocket_state == MainGUI.RocketState.Init:
            if imgui.button(icons_fontawesome.ICON_FA_ROCKET + " Launch rocket"):
                self.rocket_state = self.RocketState.Preparing
                hello_imgui.log(hello_imgui.LogLevel.warning, "Rocket is being prepared")
        elif self.rocket_state == self.RocketState.Preparing:
            imgui.text("Please Wait")
            self.rocket_progress += 0.003
            if self.rocket_progress >= 1.0:
                self.rocket_state = self.RocketState.Launched
                hello_imgui.log(hello_imgui.LogLevel.warning, "Rocket was launched")
        elif self.rocket_state == self.RocketState.Launched:
            imgui.text(icons_fontawesome.ICON_FA_ROCKET + " Rocket Launched")
            if imgui.button("Reset Rocket"):
                self.rocket_state = self.RocketState.Init
                self.rocket_progress = 0.0
        imgui.end_child()

        # Note, you can also show the tweak theme widgets via:
        imgui.begin_child("theme",(-1,self.sz2))
        imgui.text('simple')
        if imgui.button("Popup"):
            utils.push_popup(self, msgbox.msgbox, "Project opening error", "A single project directory should be provided. None provided so cannot open.", msgbox.MsgBox.error, more="Dropped paths:\n")
        imgui.end_child()

        # handle popups
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


    # Our Gui in the status bar
    def _status_bar_gui(self):
        if self.rocket_state == self.RocketState.Preparing:
            imgui.text("Rocket completion: ")
            imgui.same_line()
            imgui.progress_bar(self.rocket_progress, hello_imgui.em_to_vec2(7.0, 1.0))  # type: ignore



def splitter(split_vertically, thickness, size1, size2, min_size1, min_size2, splitter_long_axis_size = -1.0):
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