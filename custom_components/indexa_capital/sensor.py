"""Sensor platform for Indexa Capital."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AGGREGATE_DEVICE_ID,
    ATTR_INVESTED_AMOUNT,
    ATTR_LATEST_HISTORY_DATE,
    DATA_COORDINATOR,
    DOMAIN,
)
from .coordinator import IndexaPortfolioCoordinator
from .models import IndexaAccountSnapshot


@dataclass(frozen=True, kw_only=True)
class IndexaSensorEntityDescription(SensorEntityDescription):
    """Entity description for Indexa sensors."""

    value_fn: Any


ACCOUNT_SENSORS = (
    IndexaSensorEntityDescription(
        key="contributions_amount",
        translation_key="contributions_amount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda account, coordinator: account.invested_amount,
    ),
    IndexaSensorEntityDescription(
        key="performance_amount",
        translation_key="performance_amount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda account, coordinator: account.performance_amount,
    ),
    IndexaSensorEntityDescription(
        key="performance_percentage",
        translation_key="performance_percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda account, coordinator: account.performance_percentage,
    ),
)

AGGREGATE_SENSORS = (
    IndexaSensorEntityDescription(
        key="total_contributions_amount",
        translation_key="total_contributions_amount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda snapshot, coordinator: snapshot.total_contributions_amount,
    ),
    IndexaSensorEntityDescription(
        key="total_performance_amount",
        translation_key="total_performance_amount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda snapshot, coordinator: snapshot.total_performance_amount,
    ),
    IndexaSensorEntityDescription(
        key="total_performance_percentage",
        translation_key="total_performance_percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda snapshot, coordinator: snapshot.total_performance_percentage,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Indexa sensors."""
    coordinator: IndexaPortfolioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[SensorEntity] = []

    if coordinator.data:
        for account in coordinator.data.accounts:
            entities.extend(
                IndexaAccountSensor(coordinator, entry, account.account_number, description)
                for description in ACCOUNT_SENSORS
            )

    entities.extend(
        IndexaAggregateSensor(coordinator, entry, description) for description in AGGREGATE_SENSORS
    )
    async_add_entities(entities)


class IndexaBaseSensor(CoordinatorEntity[IndexaPortfolioCoordinator], SensorEntity):
    """Base class for Indexa sensors."""

    entity_description: IndexaSensorEntityDescription

    def __init__(self, coordinator: IndexaPortfolioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry


class IndexaAccountSensor(IndexaBaseSensor):
    """Per-account performance sensor."""

    def __init__(
        self,
        coordinator: IndexaPortfolioCoordinator,
        entry: ConfigEntry,
        account_number: str,
        description: IndexaSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._account_number = account_number
        self._attr_unique_id = (
            f"{coordinator.client.token_fingerprint}_{account_number}_{description.key}"
        )
        self._attr_has_entity_name = True

    @property
    def _account(self) -> IndexaAccountSnapshot:
        if not self.coordinator.data:
            raise KeyError(self._account_number)
        for account in self.coordinator.data.accounts:
            if account.account_number == self._account_number:
                return account
        raise KeyError(self._account_number)

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self._account, self.coordinator)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the native unit."""
        if self.entity_description.native_unit_of_measurement:
            return self.entity_description.native_unit_of_measurement
        return self._account.currency

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return account metadata."""
        account = self._account
        return {
            "account_number": account.account_number,
            ATTR_INVESTED_AMOUNT: account.invested_amount,
            ATTR_LATEST_HISTORY_DATE: (
                account.latest_history_date.isoformat() if account.latest_history_date else None
            ),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        account = self._account
        return DeviceInfo(
            identifiers={(DOMAIN, f"account_{account.account_number}")},
            name=account.display_name,
            manufacturer="Indexa Capital",
            model="Investment Account",
        )


class IndexaAggregateSensor(IndexaBaseSensor):
    """Aggregate portfolio sensor."""

    def __init__(
        self,
        coordinator: IndexaPortfolioCoordinator,
        entry: ConfigEntry,
        description: IndexaSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.client.token_fingerprint}_{description.key}"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> float | None:
        """Return the aggregate sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data, self.coordinator)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the native unit."""
        if self.entity_description.native_unit_of_measurement:
            return self.entity_description.native_unit_of_measurement
        return self.coordinator.data.currency if self.coordinator.data else "EUR"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return aggregate metadata."""
        latest_date = self.coordinator.data.latest_history_date if self.coordinator.data else None
        total_invested = self.coordinator.data.total_invested_amount if self.coordinator.data else 0
        return {
            ATTR_INVESTED_AMOUNT: total_invested,
            ATTR_LATEST_HISTORY_DATE: latest_date.isoformat() if latest_date else None,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return aggregate device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, AGGREGATE_DEVICE_ID)},
            name="Indexa Portfolio",
            manufacturer="Indexa Capital",
            model="Portfolio Summary",
        )
