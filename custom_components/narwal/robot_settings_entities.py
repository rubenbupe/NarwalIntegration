"""Entities backed by NarwalRobotSettings: robot options, Freo preset, language, battery."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import narwal_dock_device_info, narwal_robot_device_info
from .narwal_client.extended import CONFIG_FIELDS, LANGUAGES, FreoStrategy
from .robot_settings import NarwalRobotSettings

# Settings that belong to the base station device.
DOCK_SETTINGS = frozenset(
    {
        "auto_detergent",
        "mop_drying_strength",
        "dock_default_mode",
        "moisture_proof_pad",
        "auto_empty",
        "smart_auto_empty",
        "quiet_auto_empty",
        "dust_bag_disinfection",
        "hot_water_mop_wash",
        "station_light",
    }
)

# Settings the official app does not show; disabled by default in the registry.
ROBOT_ONLY_SETTINGS = frozenset(
    {
        "auto_detergent",
        "dirt_detection",
        "freo_intensity",
        "smart_deep_clean",
        "moisture_proof_pad",
        "robot_default_mode",
        "dust_sensor_detection",
        "recognize_all_objects",
        "precious_item_guard",
        "station_light",
    }
)

# Enum settings: option slug -> robot value.
ENUM_OPTIONS: dict[str, dict[str, int]] = {
    "mop_drying_strength": {"quiet": 1, "strong": 2, "smart": 3},
    "rewash_area": {"efficient_15m2": 1, "normal_20m2": 2, "deep_30m2": 3},
    "carpet_mode": {"cross": 1, "clean": 2, "vacuum_and_mop": 3, "avoid": 4},
    "new_carpet_mode": {"cross": 1, "clean": 2, "vacuum_and_mop": 3, "avoid": 4},
    "freo_intensity": {"quick": 1, "normal": 2, "deep": 3},
    "dock_default_mode": {
        "vacuum": 2,
        "mop": 3,
        "vacuum_and_mop": 4,
        "vacuum_then_mop": 5,
    },
    "obstacle_avoidance": {"smart": 1, "safer": 2, "off": 3},
    "robot_default_mode": {
        "vacuum": 2,
        "mop": 3,
        "vacuum_and_mop": 4,
        "vacuum_then_mop": 5,
    },
    "corner_cleaning": {"on": 1, "off": 3},
    "pet_icon": {"cat": 1, "dog": 2, "cat_and_dog": 3},
    "carpet_priority": {"default": 1, "carpet_first": 2},
    "carpet_focus": {"off": 1, "on": 2},
    "station_light": {"off": 1, "on": 2},
}

FREO_OPTIONS: dict[FreoStrategy, dict[str, int | None]] = {
    FreoStrategy.REMOP_LIMIT: {"smart": None, "1": 1, "2": 2, "3": 3},
    FreoStrategy.COVERAGE: {"smart": None, "standard": 1, "intensive": 2},
    FreoStrategy.SUCTION: {
        "smart": None,
        "quiet": 1,
        "standard": 2,
        "strong": 3,
        "super_powerful": 4,
        "max": 5,
    },
    FreoStrategy.MOP_HUMIDITY: {
        "smart": None,
        "slightly_dry": 1,
        "standard": 2,
        "high": 3,
    },
}

FREO_KEYS = {
    FreoStrategy.REMOP_LIMIT: "freo_remop_limit",
    FreoStrategy.COVERAGE: "freo_coverage",
    FreoStrategy.SUCTION: "freo_suction",
    FreoStrategy.MOP_HUMIDITY: "freo_mop_humidity",
}


class NarwalSettingsEntity(CoordinatorEntity[NarwalRobotSettings]):
    """Base for entities on the settings coordinator."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, settings: NarwalRobotSettings, key: str, dock: bool = False) -> None:
        super().__init__(settings)
        robot = settings.robot
        device_id = robot.config_entry.data["device_id"]
        self._key = key
        self._attr_unique_id = f"{device_id}_setting_{key}"
        self._attr_translation_key = None
        self._attr_device_info = (
            narwal_dock_device_info(robot) if dock else narwal_robot_device_info(robot)
        )
        if key in ROBOT_ONLY_SETTINGS:
            self._attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.robot.last_update_success
        )


class NarwalSettingSwitch(NarwalSettingsEntity, SwitchEntity):
    """A boolean robot setting."""

    def __init__(self, settings: NarwalRobotSettings, key: str) -> None:
        super().__init__(settings, key, key in DOCK_SETTINGS)
        self._attr_translation_key = key

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.config.get(self._key) if self.coordinator.data else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set(self._key, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set(self._key, False)


class NarwalSettingSelect(NarwalSettingsEntity, SelectEntity):
    """An enum robot setting."""

    def __init__(self, settings: NarwalRobotSettings, key: str) -> None:
        super().__init__(settings, key, key in DOCK_SETTINGS)
        self._attr_translation_key = key
        self._map = ENUM_OPTIONS[key]
        self._attr_options = list(self._map)

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.config.get(self._key)
        return next((slug for slug, v in self._map.items() if v == value), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set(self._key, self._map[option])


class NarwalFreoSelect(NarwalSettingsEntity, SelectEntity):
    """One Freo Mind preset strategy (smart = let the AI decide)."""

    def __init__(self, settings: NarwalRobotSettings, strategy: FreoStrategy) -> None:
        super().__init__(settings, FREO_KEYS[strategy])
        self._attr_translation_key = FREO_KEYS[strategy]
        self._strategy = strategy
        self._map = FREO_OPTIONS[strategy]
        self._attr_options = list(self._map)

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.freo.get(self._strategy)
        return next((slug for slug, v in self._map.items() if v == value), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_freo(self._strategy, self._map[option])


class NarwalLanguageSelect(NarwalSettingsEntity, SelectEntity):
    """Robot voice language."""

    _attr_translation_key = "voice_language"

    def __init__(self, settings: NarwalRobotSettings) -> None:
        super().__init__(settings, "voice_language")
        self._attr_translation_key = "voice_language"

    @property
    def options(self) -> list[str]:
        data = self.coordinator.data
        codes = data.supported_languages if data and data.supported_languages else list(LANGUAGES)
        return [LANGUAGES[c] for c in codes if c in LANGUAGES]

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        return LANGUAGES.get(data.language) if data and data.language else None

    async def async_select_option(self, option: str) -> None:
        code = next((c for c, slug in LANGUAGES.items() if slug == option), None)
        if code is None:
            raise HomeAssistantError(f"Unknown language {option}")
        await self.coordinator.async_set_language(code)


class NarwalVolumeNumber(NarwalSettingsEntity, NumberEntity):
    """Voice volume (config field 1)."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, settings: NarwalRobotSettings) -> None:
        super().__init__(settings, "volume")
        self._attr_translation_key = "volume"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.config.get("volume") if data else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set("volume", int(value))


@dataclass(frozen=True)
class BatterySensorSpec:
    key: str
    unit: str | None
    device_class: SensorDeviceClass | None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


BATTERY_SENSORS = (
    BatterySensorSpec("battery_health", PERCENTAGE, None),
    BatterySensorSpec("battery_cycles", None, None, SensorStateClass.TOTAL_INCREASING),
    BatterySensorSpec(
        "battery_temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE
    ),
    BatterySensorSpec("battery_capacity", "mAh", None),
)
BATTERY_FIELDS = {
    "battery_health": "health",
    "battery_cycles": "charge_cycles",
    "battery_temperature": "temperature",
    "battery_capacity": "full_charge_capacity",
}


class NarwalBatterySensor(NarwalSettingsEntity, SensorEntity):
    """Battery health from info/battery_info (not shown in the app)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, settings: NarwalRobotSettings, spec: BatterySensorSpec) -> None:
        super().__init__(settings, spec.key)
        self._attr_translation_key = spec.key
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._field = BATTERY_FIELDS[spec.key]

    @property
    def native_value(self):
        data = self.coordinator.data
        value = data.battery.get(self._field) if data else None
        return round(value, 1) if isinstance(value, float) else value


class NarwalActionButton(CoordinatorEntity[NarwalRobotSettings], ButtonEntity):
    """One-shot robot action."""

    _attr_has_entity_name = True

    def __init__(self, settings: NarwalRobotSettings, key: str) -> None:
        super().__init__(settings)
        robot = settings.robot
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{robot.config_entry.data['device_id']}_{key}"
        self._attr_device_info = narwal_robot_device_info(robot)

    async def async_press(self) -> None:
        client = self.coordinator.client
        if not client.robot_awake:
            await client.wake(timeout=10.0)
        if self._key == "search_pet":
            response = await client.search_pet()
        elif self._key == "stop_remote_control":
            response = await client.set_manual_control(0)
        else:  # pragma: no cover - guarded by construction
            raise HomeAssistantError(f"Unknown action {self._key}")
        NarwalRobotSettings._check(response, self._key)


def robot_settings_for(coordinator: object) -> NarwalRobotSettings | None:
    """The settings coordinator attached at setup, if any."""
    settings = getattr(coordinator, "robot_settings", None)
    return settings if isinstance(settings, NarwalRobotSettings) else None


def settings_switches(settings: NarwalRobotSettings) -> list[NarwalSettingSwitch]:
    return [
        NarwalSettingSwitch(settings, name)
        for name, spec in CONFIG_FIELDS.items()
        if spec.kind == "bool"
    ]


def settings_selects(settings: NarwalRobotSettings) -> list[SelectEntity]:
    entities: list[SelectEntity] = [NarwalSettingSelect(settings, name) for name in ENUM_OPTIONS]
    entities += [NarwalFreoSelect(settings, strategy) for strategy in FreoStrategy]
    entities.append(NarwalLanguageSelect(settings))
    return entities


def settings_numbers(settings: NarwalRobotSettings) -> list[NumberEntity]:
    return [NarwalVolumeNumber(settings)]


def settings_sensors(settings: NarwalRobotSettings) -> list[SensorEntity]:
    return [NarwalBatterySensor(settings, spec) for spec in BATTERY_SENSORS]


def settings_buttons(settings: NarwalRobotSettings) -> list[ButtonEntity]:
    return [NarwalActionButton(settings, key) for key in ("search_pet", "stop_remote_control")]
