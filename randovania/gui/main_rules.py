import collections
import dataclasses
import functools
from functools import partial
from typing import Dict, Tuple, List

from PySide2.QtCore import QRect, Qt
from PySide2.QtWidgets import QMainWindow, QLabel, QGroupBox, QGridLayout, QToolButton, QSizePolicy, QDialog, QSpinBox

from randovania.game_description.default_database import default_prime2_item_database, default_prime2_resource_database
from randovania.game_description.item.ammo import Ammo
from randovania.game_description.item.item_database import ItemDatabase
from randovania.game_description.item.major_item import MajorItemCategory, MajorItem
from randovania.game_description.resource_type import ResourceType
from randovania.gui.background_task_mixin import BackgroundTaskMixin
from randovania.gui.item_configuration_popup import ItemConfigurationPopup
from randovania.gui.main_rules_ui import Ui_MainRules
from randovania.gui.tab_service import TabService
from randovania.interface_common.options import Options
from randovania.layout.ammo_state import AmmoState
from randovania.layout.major_item_state import ENERGY_TANK_MAXIMUM_COUNT, MajorItemState
from randovania.resolver.exceptions import InvalidConfiguration
from randovania.resolver.item_pool.ammo import items_for_ammo

_EXPECTED_COUNT_TEXT_TEMPLATE = ("Each expansion will provide, on average, {per_expansion} for a total of {total}."
                                 "\n{from_items} will be provided from major items.")


def _toggle_category_visibility(category_button: QToolButton, category_box: QGroupBox):
    category_box.setVisible(not category_box.isVisible())
    category_button.setText("-" if category_box.isVisible() else "+")


class MainRulesWindow(QMainWindow, Ui_MainRules):
    _boxes_for_category: Dict[
        MajorItemCategory, Tuple[QGroupBox, QGridLayout, Dict[MajorItem, Tuple[QToolButton, QLabel]]]]

    _ammo_maximum_spinboxes: Dict[int, List[QSpinBox]]
    _ammo_pickup_widgets: Dict[Ammo, Tuple[QSpinBox, QLabel]]

    def __init__(self, tab_service: TabService, background_processor: BackgroundTaskMixin, options: Options):
        super().__init__()
        self.setupUi(self)

        self._options = options
        size_policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.gridLayout.setAlignment(Qt.AlignTop)

        # Relevant Items
        item_database = default_prime2_item_database()

        self._dark_suit = item_database.major_items["Dark Suit"]
        self._light_suit = item_database.major_items["Light Suit"]
        self._progressive_suit = item_database.major_items["Progressive Suit"]

        self._energy_tank_item = item_database.major_items["Energy Tank"]

        self._register_alternatives_events()
        self._create_categories_boxes(size_policy)
        self._create_major_item_boxes(item_database)
        self._create_energy_tank_box()
        self._create_ammo_pickup_boxes(item_database)

    def on_options_changed(self):
        # Item alternatives
        layout = self._options.layout_configuration
        self.progressive_suit_check.setChecked(layout.progressive_suit)
        self.progressive_grapple_check.setChecked(layout.progressive_grapple)
        self.split_ammo_check.setChecked(layout.split_beam_ammo)

        suit_elements = self._boxes_for_category[MajorItemCategory.SUIT][2]
        for element in suit_elements[self._dark_suit] + suit_elements[self._light_suit]:
            element.setVisible(not layout.progressive_suit)

        for element in suit_elements[self._progressive_suit]:
            element.setVisible(layout.progressive_suit)

        # Energy Tank
        major_configuration = self._options.major_items_configuration
        energy_tank_state = major_configuration.items_state[self._energy_tank_item]

        self.energy_tank_starting_spinbox.setValue(energy_tank_state.num_included_in_starting_items)
        self.energy_tank_shuffled_spinbox.setValue(energy_tank_state.num_shuffled_pickups)

        # Ammo
        ammo_provided = major_configuration.calculate_provided_ammo()
        ammo_configuration = self._options.ammo_configuration

        for ammo_item, maximum in ammo_configuration.maximum_ammo.items():
            for spinbox in self._ammo_maximum_spinboxes[ammo_item]:
                spinbox.setValue(maximum)

        previous_pickup_for_item = {}
        resource_database = default_prime2_resource_database()

        item_for_index = {
            ammo_index: resource_database.get_by_type_and_index(ResourceType.ITEM, ammo_index)
            for ammo_index in ammo_provided.keys()
        }

        print("=============================")
        for ammo, state in ammo_configuration.items_state.items():
            self._ammo_pickup_widgets[ammo][0].setValue(state.pickup_count)

            try:
                ammo_per_pickup = items_for_ammo(ammo, state, ammo_provided,
                                                 previous_pickup_for_item,
                                                 ammo_configuration.maximum_ammo)

                totals = functools.reduce(lambda a, b: [x + y for x, y in zip(a, b)],
                                          ammo_per_pickup,
                                          [0 for _ in ammo.items])

                if state.pickup_count == 0:
                    self._ammo_pickup_widgets[ammo][1].setText("No expansions will be created.")
                    continue

                self._ammo_pickup_widgets[ammo][1].setText(
                    _EXPECTED_COUNT_TEXT_TEMPLATE.format(
                        per_expansion=" and ".join(
                            "{} {}".format(
                                total // state.pickup_count,
                                item_for_index[ammo_index].long_name
                            )
                            for ammo_index, total in zip(ammo.items, totals)
                        ),
                        total=" and ".join(
                            "{} {}".format(
                                total,
                                item_for_index[ammo_index].long_name
                            )
                            for ammo_index, total in zip(ammo.items, totals)
                        ),
                        from_items=" and ".join(
                            "{} {}".format(
                                ammo_provided[ammo_index],
                                item_for_index[ammo_index].long_name
                            )
                            for ammo_index in ammo.items
                        ),
                    )
                )

            except InvalidConfiguration as invalid_config:
                self._ammo_pickup_widgets[ammo][1].setText(str(invalid_config))

    # Item Alternatives

    def _register_alternatives_events(self):
        self.progressive_suit_check.stateChanged.connect(self._persist_bool_layout_field("progressive_suit"))
        self.progressive_suit_check.clicked.connect(self._change_progressive_suit)

    def _persist_bool_layout_field(self, field_name: str):
        def bound(value: int):
            with self._options as options:
                options.set_layout_configuration_field(field_name, bool(value))

        return bound

    def _change_progressive_suit(self, has_progressive: bool):
        with self._options as options:
            major_configuration = options.major_items_configuration

            if has_progressive:
                dark_suit_state = MajorItemState()
                light_suit_state = MajorItemState()
                progressive_suit_state = MajorItemState(num_shuffled_pickups=2)
            else:
                dark_suit_state = MajorItemState(num_shuffled_pickups=1)
                light_suit_state = MajorItemState(num_shuffled_pickups=1)
                progressive_suit_state = MajorItemState()

            major_configuration = major_configuration.replace_states({
                self._dark_suit: dark_suit_state,
                self._light_suit: light_suit_state,
                self._progressive_suit: progressive_suit_state,
            })

            options.major_items_configuration = major_configuration

    # Major Items

    def _create_categories_boxes(self, size_policy):
        self._boxes_for_category = {}

        for i, major_item_category in enumerate(MajorItemCategory):
            category_button = QToolButton(self.major_items_box)
            category_button.setGeometry(QRect(20, 30, 24, 21))
            category_button.setText("+")

            category_label = QLabel(self.major_items_box)
            category_label.setSizePolicy(size_policy)
            category_label.setText(major_item_category.value)

            category_box = QGroupBox(self.major_items_box)
            category_box.setSizePolicy(size_policy)
            category_box.setObjectName(f"category_box {major_item_category}")

            category_layout = QGridLayout(category_box)
            category_layout.setObjectName(f"category_layout {major_item_category}")

            self.major_items_layout.addWidget(category_button, 2 * i + 1, 0, 1, 1)
            self.major_items_layout.addWidget(category_label, 2 * i + 1, 1, 1, 1)
            self.major_items_layout.addWidget(category_box, 2 * i + 2, 0, 1, 2)
            self._boxes_for_category[major_item_category] = category_box, category_layout, {}

            category_button.clicked.connect(partial(_toggle_category_visibility, category_button, category_box))
            category_box.setVisible(False)

    def _create_major_item_boxes(self, item_database: ItemDatabase):
        for major_item in item_database.major_items.values():
            if major_item.item_category == MajorItemCategory.ENERGY_TANK:
                continue
            category_box, category_layout, elements = self._boxes_for_category[major_item.item_category]

            item_button = QToolButton(category_box)
            item_button.setGeometry(QRect(20, 30, 24, 21))
            item_button.setText("...")

            item_label = QLabel(category_box)
            item_label.setText(major_item.name)

            i = len(elements)
            category_layout.addWidget(item_button, i, 0)
            category_layout.addWidget(item_label, i, 1)
            elements[major_item] = item_button, item_label

            item_button.clicked.connect(partial(self.show_item_popup, major_item))

    def show_item_popup(self, item: MajorItem):
        """
        Shows the ItemConfigurationPopup for the given MajorItem
        :param item:
        :return:
        """
        major_items_configuration = self._options.major_items_configuration

        popup = ItemConfigurationPopup(self, item, major_items_configuration.items_state[item])
        result = popup.exec_()

        if result == QDialog.Accepted:
            with self._options:
                self._options.major_items_configuration = major_items_configuration.replace_state_for_item(
                    item, popup.state
                )

    # Energy Tank

    def _create_energy_tank_box(self):
        category_box, category_layout, _ = self._boxes_for_category[MajorItemCategory.ENERGY_TANK]

        starting_label = QLabel(category_box)
        starting_label.setText("Starting Quantity")

        shuffled_label = QLabel(category_box)
        shuffled_label.setText("Shuffled Quantity")

        self.energy_tank_starting_spinbox = QSpinBox(category_box)
        self.energy_tank_starting_spinbox.setMaximum(ENERGY_TANK_MAXIMUM_COUNT)
        self.energy_tank_starting_spinbox.valueChanged.connect(self._on_update_starting_energy_tank)
        self.energy_tank_shuffled_spinbox = QSpinBox(category_box)
        self.energy_tank_shuffled_spinbox.setMaximum(ENERGY_TANK_MAXIMUM_COUNT)
        self.energy_tank_shuffled_spinbox.valueChanged.connect(self._on_update_shuffled_energy_tank)

        category_layout.addWidget(starting_label, 0, 0)
        category_layout.addWidget(self.energy_tank_starting_spinbox, 0, 1)
        category_layout.addWidget(shuffled_label, 1, 0)
        category_layout.addWidget(self.energy_tank_shuffled_spinbox, 1, 1)

    def _on_update_starting_energy_tank(self, value: int):
        with self._options as options:
            major_configuration = options.major_items_configuration
            options.major_items_configuration = major_configuration.replace_state_for_item(
                self._energy_tank_item,
                dataclasses.replace(major_configuration.items_state[self._energy_tank_item],
                                    num_included_in_starting_items=value)
            )

    def _on_update_shuffled_energy_tank(self, value: int):
        with self._options as options:
            major_configuration = options.major_items_configuration
            options.major_items_configuration = major_configuration.replace_state_for_item(
                self._energy_tank_item,
                dataclasses.replace(major_configuration.items_state[self._energy_tank_item],
                                    num_shuffled_pickups=value)
            )

    # Ammo

    def _create_ammo_maximum_boxes(self, item_database: ItemDatabase):
        """
        Creates the GroupBox with SpinBoxes for selecting the maximum value of all the ammo items
        :param item_database:
        :return:
        """
        resource_database = default_prime2_resource_database()
        ammo_items = {}

        for ammo in item_database.ammo.values():
            for ammo_item in ammo.items:
                ammo_items[ammo_item] = max(ammo_items.get(ammo_item, 0), ammo.maximum)

        for i, (ammo_item, maximum) in enumerate(ammo_items.items()):
            maximum_name_label = QLabel(self.maximum_count_box)
            maximum_spinbox = QSpinBox(self.maximum_count_box)

            item = resource_database.get_by_type_and_index(ResourceType.ITEM, ammo_item)
            maximum_name_label.setText(item.long_name)
            maximum_spinbox.setMaximum(maximum)
            maximum_spinbox.valueChanged.connect(partial(self._on_update_ammo_maximum_spinbox, ammo_item))

            self.maximum_count_layout.addWidget(maximum_name_label, i, 0)
            self.maximum_count_layout.addWidget(maximum_spinbox, i, 1)

            self._ammo_maximum_spinboxes[ammo_item] = maximum_spinbox

    def _create_ammo_pickup_boxes(self, item_database: ItemDatabase):
        """
        Creates the GroupBox with SpinBoxes for selecting the pickup count of all the ammo
        :param item_database:
        :return:
        """

        self._ammo_maximum_spinboxes = collections.defaultdict(list)
        self._ammo_pickup_widgets = {}

        resource_database = default_prime2_resource_database()

        for ammo in item_database.ammo.values():
            pickup_box = QGroupBox(self.ammo_box)
            pickup_box.setTitle(ammo.name)

            layout = QGridLayout(pickup_box)
            current_row = 0

            for ammo_item in ammo.items:
                item = resource_database.get_by_type_and_index(ResourceType.ITEM, ammo_item)

                target_count_label = QLabel(pickup_box)
                target_count_label.setText(f"{item.long_name} Target" if len(ammo.items) > 1 else "Target count")

                maximum_spinbox = QSpinBox(pickup_box)
                maximum_spinbox.setMaximum(ammo.maximum)
                maximum_spinbox.valueChanged.connect(partial(self._on_update_ammo_maximum_spinbox, ammo_item))
                self._ammo_maximum_spinboxes[ammo_item].append(maximum_spinbox)

                layout.addWidget(target_count_label, current_row, 0)
                layout.addWidget(maximum_spinbox, current_row, 1)
                current_row += 1

            count_label = QLabel(pickup_box)
            count_label.setText("Pickup Count")
            count_label.setToolTip("How many instances of this expansion should be placed.")

            pickup_spinbox = QSpinBox(pickup_box)
            pickup_spinbox.setMaximum(AmmoState.maximum_pickup_count())
            pickup_spinbox.valueChanged.connect(partial(self._on_update_ammo_pickup_spinbox, ammo))

            layout.addWidget(count_label, current_row, 0)
            layout.addWidget(pickup_spinbox, current_row, 1)
            current_row += 1

            expected_count = QLabel(pickup_box)
            expected_count.setWordWrap(True)
            expected_count.setText(_EXPECTED_COUNT_TEXT_TEMPLATE)
            expected_count.setToolTip("Some expansions may provide 1 extra, even with no variance, if the total count "
                                      "is not divisible by the pickup count.")
            layout.addWidget(expected_count, current_row, 0, 1, 2)
            current_row += 1

            self._ammo_pickup_widgets[ammo] = pickup_spinbox, expected_count

            self.ammo_layout.addWidget(pickup_box)

    def _on_update_ammo_maximum_spinbox(self, ammo_int: int, value: int):
        with self._options as options:
            options.ammo_configuration = options.ammo_configuration.replace_maximum_for_item(
                ammo_int, value
            )

    def _on_update_ammo_pickup_spinbox(self, ammo: Ammo, value: int):
        with self._options as options:
            ammo_configuration = options.ammo_configuration
            options.ammo_configuration = ammo_configuration.replace_state_for_ammo(
                ammo,
                dataclasses.replace(ammo_configuration.items_state[ammo], pickup_count=value)
            )