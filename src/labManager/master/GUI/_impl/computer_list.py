from imgui_bundle import icons_fontawesome, imgui
from typing import Callable, Tuple

from ....common import structs
from . import utils

class ComputerList():
    def __init__(self,
                 items: dict[int, structs.KnownClient],
        selected_items: dict[int, bool],
        info_callback: Callable = None):

        self.items = items
        self.selected_items = selected_items
        self.info_callback  = info_callback

        self.sorted_ids: list[int] = []
        self._last_clicked_id: int = None
        self._require_sort: bool = True

        self.clr_off = (1., 0.2824, 0.2824, 1.)
        self.clr_on  = (0.0588, 0.4510, 0.0471, 1.)

        self._view_column_count = 3
        self._num_items = len(self.items)
        self.table_flags: int = (
            imgui.TableFlags_.scroll_x |
            imgui.TableFlags_.scroll_y |
            imgui.TableFlags_.hideable |
            imgui.TableFlags_.sortable |
            imgui.TableFlags_.resizable |
            imgui.TableFlags_.sort_multi |
            imgui.TableFlags_.reorderable |
            imgui.TableFlags_.sizing_fixed_fit |
            imgui.TableFlags_.no_host_extend_y |
            imgui.TableFlags_.no_borders_in_body_until_resize
        )

    def draw(self):
        if imgui.begin_table(
            f"##item_list",
            column=self._view_column_count,
            flags=self.table_flags,
        ):
            if (num_items := len(self.items)) != self._num_items:
                self._num_items = num_items
                self._require_sort = True
            frame_height = imgui.get_frame_height()

            # Setup
            checkbox_width = frame_height
            imgui.table_setup_column("Selector", imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_sort | imgui.TableColumnFlags_.no_resize | imgui.TableColumnFlags_.no_reorder, init_width_or_weight=checkbox_width)  # 0
            imgui.table_setup_column("Name", imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_resize)  # 1
            imgui.table_setup_column("IP", imgui.TableColumnFlags_.default_hide) # 2

            # Enabled columns
            if imgui.table_get_column_flags(0) & imgui.TableColumnFlags_.is_enabled:
                imgui.table_setup_scroll_freeze(1, 1)  # Sticky column headers and selector row
            else:
                imgui.table_setup_scroll_freeze(0, 1)  # Sticky column headers

            # Sorting
            sort_specs = imgui.table_get_sort_specs()
            sorted_ids_len = len(self.sorted_ids)
            self._sort_items(sort_specs)
            if len(self.sorted_ids) < sorted_ids_len:
                # we've just filtered out some items from view. Deselect those
                # NB: will also be triggered when removing an item, doesn't matter
                for id in self.items:
                    if id not in self.sorted_ids:
                        self.selected_items[id] = False

            # Headers
            imgui.table_next_row(imgui.TableRowFlags_.headers)
            for i in range(self._view_column_count):
                imgui.table_set_column_index(i)
                column_name = imgui.table_get_column_name(i)
                if i==0:  # checkbox column: reflects whether all, some or none of visible items are selected, and allows selecting all or none
                    # get state
                    num_selected = sum([self.selected_items[id] for id in self.sorted_ids])
                    if num_selected==0:
                        # none selected
                        multi_selected_state = -1
                    elif num_selected==len(self.sorted_ids):
                        # all selected
                        multi_selected_state = 1
                    else:
                        # some selected
                        multi_selected_state = 0

                    if multi_selected_state==0:
                        imgui.internal.push_item_flag(imgui.internal.ItemFlags_.mixed_value, True)
                    clicked, new_state = my_checkbox(f"##header_checkbox", multi_selected_state==1, frame_size=(0,0), do_vertical_align=False)
                    if multi_selected_state==0:
                        imgui.internal.pop_item_flag()

                    if clicked:
                        utils.set_all(self.selected_items, new_state, subset = self.sorted_ids)
                else:
                    imgui.table_header(column_name)

            # Loop rows
            any_selectable_clicked = False
            if self.sorted_ids and self._last_clicked_id not in self.sorted_ids:
                # default to topmost if last_clicked unknown, or no longer on screen due to filter
                self._last_clicked_id = self.sorted_ids[0]
            for id in self.sorted_ids:
                imgui.table_next_row()

                item = self.items[id]
                num_columns_drawn = 0
                selectable_clicked = False
                checkbox_clicked, checkbox_hovered = False, False
                info_button_hovered = False
                has_drawn_hitbox = False
                for ri in range(self._view_column_count+1):
                    if not (imgui.table_get_column_flags(ri) & imgui.TableColumnFlags_.is_enabled):
                        continue
                    imgui.table_set_column_index(ri)

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
                        selectable_clicked, selectable_out = imgui.selectable(f"##{id}_hitbox", self.selected_items[id], flags=imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap|imgui.internal.SelectableFlagsPrivate_.select_on_click, size=(0,frame_height+cell_padding_y))
                        # instead override table row background color
                        if selectable_out:
                            pass#imgui.table_set_bg_color(imgui.TableBgTarget_.row_bg0, imgui.color_convert_float4_to_u32(color_selected_row))
                        elif imgui.is_item_hovered():
                            pass#imgui.table_set_bg_color(imgui.TableBgTarget_.row_bg0, imgui.color_convert_float4_to_u32(color_hovered_row))
                        imgui.set_cursor_pos_y(cur_pos_y)   # instead of imgui.same_line(), we just need this part of its effect
                        imgui.pop_style_var(3)
                        selectable_right_clicked = self._handle_item_hitbox_events(id)
                        has_drawn_hitbox = True

                    if num_columns_drawn==1:
                        # (Invisible) button because it aligns the following draw calls to center vertically
                        imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                        imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,imgui.get_style().frame_padding.y))
                        imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,imgui.get_style().item_spacing.y))
                        imgui.push_style_color(imgui.Col_.button, (0.,0.,0.,0.))
                        imgui.button(f"##{item.id}_id", size=(imgui.FLT_MIN, 0))
                        imgui.pop_style_color()
                        imgui.pop_style_var(3)

                        imgui.same_line()

                    match ri:
                        case 0:
                            # Selector
                            checkbox_clicked, checkbox_out = my_checkbox(f"##{id}_selected", self.selected_items[id], frame_size=(0,0))
                            checkbox_hovered = imgui.is_item_hovered()
                        case 1:
                            # Name
                            self._draw_computer_info(item)
                            imgui.same_line()
                            self._draw_item_info_button(id, label=icons_fontawesome.ICON_FA_INFO_CIRCLE)
                            info_button_hovered = imgui.is_item_hovered()
                        case 2:
                            # IP
                            imgui.text(item.client.host if item.client else '')
                    num_columns_drawn+=1

                # handle selection logic
                # NB: the part of this logic that has to do with right-clicks is in handle_item_hitbox_events()
                # NB: any_selectable_clicked is just for handling clicks not on any item
                any_selectable_clicked = any_selectable_clicked or selectable_clicked or selectable_right_clicked

                self._last_clicked_id = utils.selectable_item_logic(
                    id, self.selected_items, self._last_clicked_id, self.sorted_ids,
                    selectable_clicked, selectable_out, overlayed_hovered=checkbox_hovered or info_button_hovered,
                    overlayed_clicked=checkbox_clicked, new_overlayed_state=checkbox_out
                    )

                # further deal with doubleclick on item
                if selectable_clicked and not checkbox_hovered: # don't enter this branch if interaction is with checkbox on the table row
                    if not imgui.get_io().key_ctrl and not imgui.get_io().key_shift and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
                        self._show_item_info(id)

            last_y = imgui.get_cursor_screen_pos().y
            imgui.end_table()

            # handle click in table area outside header+contents:
            # deselect all, and if right click, show popup
            # check mouse is below bottom of last drawn row so that clicking on the one pixel empty space between selectables
            # does not cause everything to unselect or popup to open
            if imgui.is_item_clicked(imgui.MouseButton_.left) and not any_selectable_clicked and imgui.get_io().mouse_pos.y>last_y:  # NB: table header is not signalled by is_item_clicked(), so this works correctly
                utils.set_all(self.selected_items, False)

            # show menu when right-clicking the empty space
            # TODO

    def _handle_item_hitbox_events(self, id: int):
        right_clicked = False
        # Right click = context menu
        if imgui.begin_popup_context_item(f"##{id}_context"):
            # update selected items. same logic as windows explorer:
            # 1. if right-clicked on one of the selected items, regardless of what modifier is pressed, keep selection as is
            # 2. if right-clicked elsewhere than on one of the selected items:
            # 2a. if control is down pop up right-click menu for the selected items.
            # 2b. if control not down, deselect everything except clicked item (if any)
            # NB: popup not shown when shift or control are down, do not know why...
            if not self.selected_items[id] and not imgui.get_io().key_ctrl:
                utils.set_all(self.selected_items, False)
                self.selected_items[id] = True

            right_clicked = True
            self._draw_item_context_menu(id)
            imgui.end_popup()
        return right_clicked

    def _draw_computer_info(self, client: structs.KnownClient):
        is_online = client.client is not None
        et_is_on  = is_online and client.client.eye_tracker is not None and client.client.eye_tracker.online
        prepend = icons_fontawesome.ICON_FA_EYE
        clrs = []
        if is_online:
            prepend += icons_fontawesome.ICON_FA_PLAY
            clrs.append(self.clr_on if et_is_on else self.clr_off)
            clrs.append(self.clr_on)
        else:
            prepend += icons_fontawesome.ICON_FA_POWER_OFF
            clrs.append(self.clr_off)
            clrs.append(self.clr_off)

        # eye tracker
        imgui.text_colored(clrs[0], prepend[0]+' ')
        if et_is_on and imgui.is_item_hovered():
            et = client.client.eye_tracker
            info = f'{et.model} @ {et.frequency}Hz\n({et.firmware_version}, {et.serial})'
            utils.draw_tooltip(info)
        imgui.same_line()
        # computer
        imgui.begin_group()
        imgui.text_colored(clrs[1], prepend[1]+' ')
        imgui.same_line()
        # name
        imgui.text(client.name)
        imgui.end_group()
        if client.client and imgui.is_item_hovered():
            info = f'{client.client.host}:{client.client.port}'
            utils.draw_tooltip(info)

    def _draw_item_info_button(self, id: int, label):
        clicked = imgui.button(f"{label}##{id}_info")
        if clicked:
            self._show_item_info(id)
        return clicked

    def _show_item_info(self, id):
        self.info_callback(self.items[id])

    def _draw_item_context_menu(self, id):
        pass

    def _sort_items(self, sort_specs_in: imgui.TableSortSpecs):
        if sort_specs_in.specs_dirty or self._require_sort:
            ids = list(self.items)
            sort_specs = [sort_specs_in.get_specs(i) for i in range(sort_specs_in.specs_count)]
            for sort_spec in reversed(sort_specs):
                match sort_spec.column_index:
                    case 2:     # IP
                        key = lambda id: self.items[id].client.host if self.items[id].client else "zzz" # sort last if not found
                    case _:     # Name and all others
                        key = lambda id: self.items[id].name.lower()
                ids.sort(key=key, reverse=bool(sort_spec.get_sort_direction() - 1))
            self.sorted_ids = ids
            sort_specs_in.specs_dirty = False
            self._require_sort = False

def my_checkbox(label: str, state: bool, frame_size: Tuple=None, do_vertical_align=True):
    style = imgui.get_style()
    if state:
        imgui.push_style_color(imgui.Col_.frame_bg_hovered, style.color_(imgui.Col_.button_hovered))
        imgui.push_style_color(imgui.Col_.frame_bg, style.color_(imgui.Col_.button_hovered))
        imgui.push_style_color(imgui.Col_.check_mark, style.color_(imgui.Col_.text))
    if frame_size is not None:
        frame_padding = [style.frame_padding.x, style.frame_padding.y]
        imgui.push_style_var(imgui.StyleVar_.frame_padding, frame_size)
        imgui.push_style_var(imgui.StyleVar_.item_spacing, (0.,0.))
        imgui.begin_group()
        if do_vertical_align:
            imgui.dummy((0,frame_padding[1]))
        imgui.dummy((frame_padding[0],0))
        imgui.same_line()
    result = imgui.checkbox(label, state)
    if frame_size is not None:
        imgui.end_group()
        imgui.pop_style_var(2)
    if state:
        imgui.pop_style_color(3)
    return result