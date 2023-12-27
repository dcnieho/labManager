import pathlib
import typing
import string
from imgui_bundle import imgui, icons_fontawesome
import sys
import natsort
import mimetypes
import datetime
import re
from dataclasses import dataclass

from labManager.common import structs
from . import utils


DIR_ICON = icons_fontawesome.ICON_FA_FOLDER + "  "
FILE_ICON = icons_fontawesome.ICON_FA_FILE + "  "


@dataclass
class DirEntryWithCache(structs.DirEntry):
    # display fields
    display_name: str
    ctime_str: str
    mtime_str: str
    size_str: str

    def __init__(self, item: structs.DirEntry):
        super().__init__(item.name, item.is_dir, item.full_path, item.ctime, item.mtime, item.size, item.mime_type)

        # prep display strings
        self.display_name   = (DIR_ICON if self.is_dir else FILE_ICON)+self.name
        self.ctime_str      = datetime.datetime.fromtimestamp(self.ctime).strftime("%Y-%m-%d %H:%M:%S")
        self.mtime_str      = datetime.datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S")
        # size
        if not self.is_dir:
            unit = 1024**2
            orig = "%.1f KiB" % ((1024 * self.size / unit))
            while True:
                # add commas as thousands separators, if any are needed
                new = re.sub(r"^(-?\d+)(\d{3})", r"\g<1>,\g<2>", orig)
                if orig == new:
                    break
                orig = new
            self.size_str = new
        else:
            self.size_str = None


class FilePicker:
    default_flags: int = (
        imgui.WindowFlags_.no_collapse |
        imgui.WindowFlags_.no_saved_settings
    )

    def __init__(self, title="File picker", start_dir: str | pathlib.Path = None, callback: typing.Callable = None, allow_multiple = True, custom_popup_flags=0):
        self.title = title
        self.elapsed = 0.0
        self.callback = callback

        self.items: dict[int, DirEntryWithCache] = {}
        self.selected: dict[int, bool] = {}
        self.allow_multiple = allow_multiple
        self.msg: str = None
        self.require_sort = False
        self.sorted_items: list[int] = []
        self.last_clicked_id: int = None

        self.loc: pathlib.Path = None
        self.refreshing = False
        self.new_loc = False
        self.predicate = None
        self.default_flags = custom_popup_flags or FilePicker.default_flags
        self.platform_is_windows = sys.platform.startswith("win")
        # only relevant on Windows
        self.drives: list[str] = []
        self.current_drive = 0

        self.goto(start_dir or '.')

    # if passed a single directory will show that directory
    # if passed a single file, or multiple files and/or directories, will
    # open the parent of those and select them (kinda like "show in folder")
    def set_dir(self, paths: pathlib.Path | list[pathlib.Path]):
        if not isinstance(paths,list):
            paths = [paths]
        paths = [pathlib.Path(p) for p in paths]

        if len(paths)==1 and paths[0].is_dir():
            self.goto(paths[0])
        else:
            self.goto(paths[0].parent)
            # select dropped items that match predicate (if any)
            self._select_paths(paths)

    def goto(self, loc: str | pathlib.Path):
        loc = pathlib.Path(loc)
        if loc.is_file():
            loc = loc.parent
        if loc is None:
            loc = pathlib.Path('.')

        loc = loc.resolve()
        if loc != self.loc:
            self.loc = loc
            self.new_loc = True
            # changing location clears selection
            self.selected = {}
            # load new directory
            self.refresh()

    def refresh(self):
        self.refreshing = True
        try:
            items = []
            for item in self.loc.iterdir():
                stat = item.stat()
                items.append(structs.DirEntry(item.name,item.is_dir(),item,
                                              stat.st_ctime,stat.st_mtime,stat.st_size,
                                              mimetypes.guess_type(item)[0]))
        except Exception as exc:
            items = exc
        self._refresh_path_done(items)

        # also update drives
        if self.platform_is_windows:
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if pathlib.Path(drive).exists():
                    drives.append(drive)
            self._refresh_drives_done(drives)

    def _refresh_path_done(self, items: list[DirEntryWithCache]|Exception):
        previously_selected = []
        if not self.new_loc:
            previously_selected = [self.items[iid].full_path for iid in self.items if iid in self.selected and self.selected[iid]]
        self.items.clear()
        self.selected.clear()
        self.msg = None
        if isinstance(items, Exception):
            self.msg = f"Cannot open this folder!\n:{items}"
        else:
            self.items = {i:DirEntryWithCache(item) for i,item in enumerate(items)}
            self.selected = {k:False for k in self.items}
            if not self.items:
                self.msg = "This folder is empty!"

        # if refreshed the same directory, restore old selection
        self._select_paths(previously_selected)

        self.require_sort = True
        self.new_loc = False
        self.refreshing = False

    def _refresh_drives_done(self, drives: list[str]):
        # refresh drives and set up directory selector
        self.drives = drives
        for i,d in enumerate(self.drives):
            if str(self.loc).startswith(d):
                self.current_drive = i
                break

    def _select_paths(self, paths: list[pathlib.Path]):
        got_one = False
        for path in paths:
            for iid in self.items:
                entry = self.items[iid]
                if entry.full_path==path and (not self.predicate or self.predicate(iid)):
                    self.selected[iid] = True
                    got_one = True
                    break
            if not self.allow_multiple and got_one:
                break

    def tick(self):
        # Auto refresh
        self.elapsed += imgui.get_io().delta_time
        if self.elapsed > 2:
            self.elapsed = 0.0
            self.refresh()

        # Setup popup
        if not imgui.is_popup_open(self.title):
            imgui.open_popup(self.title)
        cancelled = closed = False
        opened = 1
        size = imgui.get_io().display_size
        size.x *= .7
        size.y *= .7
        imgui.set_next_window_size(size, cond=imgui.Cond_.appearing)
        if imgui.begin_popup_modal(self.title, True, flags=self.default_flags)[0]:
            cancelled = closed = utils.close_weak_popup(check_click_outside=False)
            imgui.begin_group()
            # Up button
            if imgui.button(icons_fontawesome.ICON_FA_ARROW_UP):
                self.goto(self.loc.parent)
            # Refresh button
            imgui.same_line()
            if imgui.button(icons_fontawesome.ICON_FA_REDO):
                self.refresh()
            # Drive selector
            if self.drives:
                imgui.same_line()
                imgui.set_next_item_width(imgui.get_font_size() * 4)
                changed, value = imgui.combo("##drive_selector", self.current_drive, self.drives)
                if changed:
                    self.goto(self.drives[value])
            # Location bar
            imgui.same_line()
            imgui.set_next_item_width(imgui.get_content_region_avail().x)
            confirmed, loc = imgui.input_text("##location_bar", str(self.loc), flags=imgui.InputTextFlags_.enter_returns_true)
            if imgui.begin_popup_context_item(f"##location_context"):
                if imgui.selectable(icons_fontawesome.ICON_FA_PASTE+" Paste", False)[0] and (loc := imgui.get_clipboard_text()):
                    confirmed = True
                imgui.end_popup()
            if confirmed:
                self.goto(loc)
            imgui.end_group()

            # entry list
            num_selected = 0
            button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_BAN+" Cancel")
            bottom_margin = button_text_size.y+imgui.get_style().frame_padding.y*2+imgui.get_style().item_spacing.y
            imgui.begin_child("##folder_contents", size=(imgui.get_item_rect_size().x, -bottom_margin))
            if self.msg:
                imgui.text_unformatted(self.msg)
            else:
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
                if imgui.begin_table(f"##folder_list",column=5+self.allow_multiple,flags=table_flags):
                    frame_height = imgui.get_frame_height()

                    # Setup
                    checkbox_width = frame_height
                    if self.allow_multiple:
                        imgui.table_setup_column("Selector", imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_sort | imgui.TableColumnFlags_.no_resize | imgui.TableColumnFlags_.no_reorder, init_width_or_weight=checkbox_width)  # 0
                    imgui.table_setup_column("Name", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 1
                    imgui.table_setup_column("Date created", imgui.TableColumnFlags_.default_hide)  # 2
                    imgui.table_setup_column("Date modified")  # 3
                    imgui.table_setup_column("Type")  # 4
                    imgui.table_setup_column("Size")  # 5
                    imgui.table_setup_scroll_freeze(int(self.allow_multiple), 1)  # Sticky column headers and selector row

                    sort_specs = imgui.table_get_sort_specs()
                    self.sort_items(sort_specs)

                    # Headers
                    imgui.table_next_row(imgui.TableRowFlags_.headers)
                    # checkbox column: reflects whether all, some or none of visible recordings are selected, and allows selecting all or none
                    num_selected = sum([self.selected[iid] for iid in self.selected])
                    if self.allow_multiple:
                        imgui.table_set_column_index(0)
                        # determine state
                        if self.predicate:
                            num_items = sum([self.predicate(iid) for iid in self.items])
                        else:
                            num_items = len(self.items)
                        if num_selected==0:
                            # none selected
                            multi_selected_state = -1
                        elif num_selected==num_items:
                            # all selected
                            multi_selected_state = 1
                        else:
                            # some selected
                            multi_selected_state = 0

                        if multi_selected_state==0:
                            imgui.internal.push_item_flag(imgui.internal.ItemFlags_.mixed_value, True)
                        clicked, new_state = utils.my_checkbox(f"##header_checkbox", multi_selected_state==1, frame_size=(0,0), do_vertical_align=False)
                        if multi_selected_state==0:
                            imgui.internal.pop_item_flag()

                        if clicked:
                            utils.set_all(self.selected, new_state, subset = self.sorted_items, predicate=self.predicate)

                    for i in range(5):
                        imgui.table_set_column_index(i+self.allow_multiple)
                        imgui.table_header(imgui.table_get_column_name(i+self.allow_multiple))


                    # Loop rows
                    any_selectable_clicked = False
                    if self.sorted_items and self.last_clicked_id not in self.sorted_items:
                        # default to topmost if last_clicked unknown, or no longer on screen due to filter
                        self.last_clicked_id = self.sorted_items[0]
                    for iid in self.sorted_items:
                        imgui.table_next_row()

                        selectable_clicked = False
                        checkbox_clicked, checkbox_hovered, checkbox_out = False, False, False
                        has_drawn_hitbox = False

                        disable_item = self.predicate and not self.predicate(iid)
                        if disable_item:
                            imgui.internal.push_item_flag(imgui.internal.ItemFlags_.disabled, True)
                            imgui.push_style_var(imgui.StyleVar_.alpha, imgui.get_style().alpha * 0.5)

                        for ci in range(5+self.allow_multiple):
                            if not (imgui.table_get_column_flags(ci) & imgui.TableColumnFlags_.is_enabled):
                                continue
                            imgui.table_set_column_index(ci)

                            # Row hitbox
                            if not has_drawn_hitbox:
                                # hitbox needs to be drawn before anything else on the row so that, together with imgui.set_item_allow_overlap(), hovering button
                                # or checkbox on the row will still be correctly detected.
                                # this is super finicky, but works. The below together with using a height of frame_height+cell_padding_y
                                # makes the table row only cell_padding_y/2 longer. The whole row is highlighted correctly
                                cell_padding_y = imgui.get_style().cell_padding.y
                                cur_pos_y = imgui.get_cursor_pos_y()
                                imgui.set_cursor_pos_y(cur_pos_y - cell_padding_y/2)
                                imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                                imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,0.))
                                imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,cell_padding_y))
                                selectable_clicked, selectable_out = imgui.selectable(f"##{iid}_hitbox", self.selected[iid], flags=imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap|imgui.internal.SelectableFlagsPrivate_.select_on_click, size=(0,frame_height+cell_padding_y))
                                imgui.set_cursor_pos_y(cur_pos_y)   # instead of imgui.same_line(), we just need this part of its effect
                                imgui.pop_style_var(3)
                                has_drawn_hitbox = True

                            if ci==int(self.allow_multiple):
                                # (Invisible) button because it aligns the following draw calls to center vertically
                                imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                                imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,imgui.get_style().frame_padding.y))
                                imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,imgui.get_style().item_spacing.y))
                                imgui.push_style_color(imgui.Col_.button, (0.,0.,0.,0.))
                                imgui.button(f"##{iid}_id", size=(imgui.FLT_MIN,0))
                                imgui.pop_style_color()
                                imgui.pop_style_var(3)

                                imgui.same_line()

                            match ci+int(not self.allow_multiple):
                                case 0:
                                    # Selector
                                    checkbox_clicked, checkbox_out = utils.my_checkbox(f"##{iid}_selected", self.selected[iid], frame_size=(0,0))
                                    checkbox_hovered = imgui.is_item_hovered()
                                case 1:
                                    # Name
                                    imgui.text(self.items[iid].display_name)
                                case 2:
                                    # Date created
                                    imgui.text(self.items[iid].ctime_str)
                                case 3:
                                    # Date modified
                                    imgui.text(self.items[iid].mtime_str)
                                case 4:
                                    # Type
                                    if self.items[iid].mime_type:
                                        imgui.text(self.items[iid].mime_type)
                                case 5:
                                    # Size
                                    if self.items[iid].size_str:
                                        imgui.text(self.items[iid].size_str)

                        if disable_item:
                            imgui.internal.pop_item_flag()
                            imgui.pop_style_var()

                        # handle selection logic
                        # NB: any_selectable_clicked is just for handling clicks not on any item
                        any_selectable_clicked = any_selectable_clicked or selectable_clicked
                        self.last_clicked_id = utils.selectable_item_logic(
                            iid, self.selected, self.last_clicked_id, self.sorted_items,
                            selectable_clicked, selectable_out, allow_multiple=self.allow_multiple,
                            overlayed_hovered=checkbox_hovered, overlayed_clicked=checkbox_clicked, new_overlayed_state=checkbox_out
                            )

                        # further deal with doubleclick on item
                        if selectable_clicked and not checkbox_hovered: # don't enter this branch if interaction is with checkbox on the table row
                            if not imgui.get_io().key_ctrl and not imgui.get_io().key_shift and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
                                if self.items[iid].is_dir:
                                    self.goto(self.items[iid].full_path)
                                    break
                                else:
                                    utils.set_all(self.selected, False)
                                    self.selected[iid] = True
                                    imgui.close_current_popup()
                                    closed = True

                    last_y = imgui.get_cursor_screen_pos().y
                    imgui.end_table()

                    # handle click in table area outside header+contents:
                    # deselect all, and if right click, show popup
                    # check mouse is below bottom of last drawn row so that clicking on the one pixel empty space between selectables
                    # does not cause everything to unselect or popup to open
                    if imgui.is_item_clicked() and not any_selectable_clicked and imgui.get_io().mouse_pos.y>last_y:  # left mouse click (NB: table header is not signalled by is_item_clicked(), so this works correctly)
                        utils.set_all(self.selected, False)

            imgui.end_child()

            # Cancel button
            if imgui.button(icons_fontawesome.ICON_FA_BAN+" Cancel"):
                imgui.close_current_popup()
                cancelled = closed = True
            # Ok button
            imgui.same_line()
            num_selected = sum([self.selected[iid] for iid in self.selected])
            disable_ok = not num_selected
            if disable_ok:
                imgui.internal.push_item_flag(imgui.internal.ItemFlags_.disabled, True)
                imgui.push_style_var(imgui.StyleVar_.alpha, imgui.get_style().alpha *  0.5)
            if imgui.button(icons_fontawesome.ICON_FA_CHECK+" Ok"):
                imgui.close_current_popup()
                closed = True
            if disable_ok:
                imgui.internal.pop_item_flag()
                imgui.pop_style_var()
            # Selected text
            imgui.same_line()
            imgui.text(f"  Selected {num_selected} items")
        else:
            opened = 0
            cancelled = closed = True
        if closed:
            if not cancelled and self.callback:
                selected = [self.items[iid].full_path for iid in self.items if iid in self.selected and self.selected[iid]]
                self.callback(selected if selected else None)
        return opened, closed

    def sort_items(self, sort_specs_in: imgui.TableSortSpecs):
        if sort_specs_in.specs_dirty or self.require_sort:
            ids = list(self.items)
            sort_specs = [sort_specs_in.get_specs(i) for i in range(sort_specs_in.specs_count)]
            for sort_spec in reversed(sort_specs):
                match sort_spec.column_index+int(not self.allow_multiple):
                    case 2:     # Date created
                        key = lambda iid: self.items[iid].ctime
                    case 3:     # Date modified
                        key = lambda iid: self.items[iid].mtime
                    case 4:     # Type
                        key = lambda iid: m if (m:=self.items[iid].mime_type) else ''
                    case 5:     # Size
                        key = lambda iid: self.items[iid].size

                    case _:     # Name and all others
                        key = natsort.os_sort_keygen(key=lambda iid: self.items[iid].full_path)

                ids.sort(key=key, reverse=bool(sort_spec.get_sort_direction() - 1))

            # finally, always sort dirs first
            ids.sort(key=lambda iid: self.items[iid].is_dir, reverse=True)
            self.sorted_items = ids
            sort_specs_in.specs_dirty = False
            self.require_sort = False
