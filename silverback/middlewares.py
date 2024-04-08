from typing import Any, Optional, Tuple

from ape.logging import logger
from ape.types import ContractLog
from ape.utils import ManagerAccessMixin
from eth_utils.conversions import to_hex
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from silverback.persistence import HandlerResult
from silverback.types import SilverbackID, TaskType, handler_id_block, handler_id_event
from silverback.utils import hexbytes_dict


def resolve_task(message: TaskiqMessage) -> Tuple[str, Optional[int], Optional[int]]:
    block_number = message.labels.get("number") or message.labels.get("block")
    log_index = message.labels.get("log_index")
    task_id = message.task_name

    if log_index:
        # TODO: Should standardize on event signature here instead of name in case of overloading
        task_id = handler_id_event(message.args[0].contract_address, message.args[0].event_name)

    elif block_number:
        task_id = handler_id_block(block_number)

    return task_id, block_number, log_index


class SilverbackMiddleware(TaskiqMiddleware, ManagerAccessMixin):
    def __init__(self, *args, **kwargs):
        def compute_block_time() -> int:
            genesis = self.chain_manager.blocks[0]
            head = self.chain_manager.blocks.head

            if not head.number or head.number == 0:
                return 10

            return int((head.timestamp - genesis.timestamp) / head.number)

        settings = kwargs.pop("silverback_settings")

        self.block_time = self.chain_manager.provider.network.block_time or compute_block_time()
        self.ident = SilverbackID.from_settings(settings)
        self.persistence = settings.get_persistent_store()

    def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        # TODO: Necessary because bytes/HexBytes doesn't encode/deocde well for some reason
        def fix_dict(data: dict, recurse_count: int = 0) -> dict:
            fixed_data: dict[str, Any] = {}
            for name, value in data.items():
                if isinstance(value, bytes):
                    fixed_data[name] = to_hex(value)
                elif isinstance(value, dict):
                    if recurse_count > 3:
                        raise RecursionError("Event object is too deep")
                    fixed_data[name] = fix_dict(value, recurse_count + 1)
                else:
                    fixed_data[name] = value

            return fixed_data

        message.args = [(fix_dict(arg) if isinstance(arg, dict) else arg) for arg in message.args]

        return message

    def _create_label(self, message: TaskiqMessage) -> str:
        if labels_str := (
            ",".join(f"{k}={v}" for k, v in message.labels.items() if k != "task_name")
        ):
            return f"{message.task_name}[{labels_str}]"

        else:
            return message.task_name

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        message.labels["task_name"] = message.task_name
        task_type = message.labels.pop("task_type", "<unknown>")

        # NOTE: Don't compare `str` to `TaskType` using `is`
        if task_type == TaskType.NEW_BLOCKS:
            # NOTE: Necessary because we don't know the exact block class
            message.args[0] = self.provider.network.ecosystem.decode_block(
                hexbytes_dict(message.args[0])
            )
            message.labels["number"] = str(message.args[0].number)
            message.labels["hash"] = message.args[0].hash.hex()

        elif "event" in message.task_name:
            # NOTE: Just in case the user doesn't specify type as `ContractLog`
            message.args[0] = ContractLog.model_validate(message.args[0])
            message.labels["block"] = str(message.args[0].block_number)
            message.labels["txn_id"] = message.args[0].transaction_hash
            message.labels["log_index"] = str(message.args[0].log_index)

        logger.debug(f"{self._create_label(message)} - Started")
        return message

    def post_execute(self, message: TaskiqMessage, result: TaskiqResult):
        percentage_time = 100 * (result.execution_time / self.block_time)
        logger.success(
            f"{self._create_label(message)} "
            f"- {result.execution_time:.3f}s ({percentage_time:.1f}%)"
        )

    async def post_save(self, message: TaskiqMessage, result: TaskiqResult):
        if not self.persistence:
            return

        handler_id, block_number, log_index = resolve_task(message)

        handler_result = HandlerResult.from_taskiq(
            self.ident, handler_id, block_number, log_index, result
        )

        try:
            await self.persistence.add_result(handler_result)
        except Exception as err:
            logger.error(f"Error storing result: {err}")

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult,
        exception: BaseException,
    ):
        percentage_time = 100 * (result.execution_time / self.block_time)
        logger.error(
            f"{self._create_label(message)} "
            f"- {result.execution_time:.3f}s ({percentage_time:.1f}%)"
        )
        # NOTE: Unless stdout is ignored, error traceback appears in stdout
