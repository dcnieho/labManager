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

        # Status bar, idle throttling
        runner_params.imgui_window_params.show_status_bar = False
        runner_params.imgui_window_params.show_status_fps = False
        runner_params.fps_idling.enable_idling = False

        # Menu bar
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
        self.computer_list.label = "Commands"
        self.computer_list.dock_space_name = "LeftSpace"
        self.computer_list.gui_function = self._command_gui
        # Other window will be placed in "MainDockSpace" on the right
        temp = hello_imgui.DockableWindow()
        temp.label = "Dear ImGui Demo"
        temp.dock_space_name = "MainDockSpace"
        temp.gui_function = imgui.show_demo_window

        # Finally, transmit these windows to HelloImGui
        runner_params.docking_params.dockable_windows = [
            self.computer_list,
            temp,
        ]

        ################################################################################################
        # Part 3: Run the app
        ################################################################################################
        addons_params = immapp.AddOnsParams()
        addons_params.with_markdown = True
        immapp.run(runner_params, addons_params)

    # CommandGui: the widgets on the left panel
    def _command_gui(self):
        if imgui.button("Add window"):
            temp2 = hello_imgui.DockableWindow()
            temp2.label = "Test"
            temp2.dock_space_name = "MainDockSpace"
            temp2.gui_function = self._simple
            wins = hello_imgui.get_runner_params().docking_params.dockable_windows
            wins.append(temp2)
            hello_imgui.get_runner_params().docking_params.dockable_windows = wins


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

    def _simple(self):
        imgui.text('simple')



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