from typing import Any

from ape.logging import logger
from ape.types import ContractLog
from ape.utils import ManagerAccessMixin
from eth_utils.conversions import to_hex
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from silverback.types import TaskType
from silverback.utils import hexbytes_dict


class SilverbackMiddleware(TaskiqMiddleware, ManagerAccessMixin):
    def __init__(self, *args, **kwargs):
        def compute_block_time() -> int:
            genesis = self.chain_manager.blocks[0]
            head = self.chain_manager.blocks.head

            if not head.number or head.number == 0:
                return 10

            return int((head.timestamp - genesis.timestamp) / head.number)

        self.block_time = self.chain_manager.provider.network.block_time or compute_block_time()

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
        if labels_str := ",".join(
            # NOTE: Have to add extra quotes around event signatures so they display as a string
            f"{k}={v}" if k != "event_signature" else f'{k}="{v}"'
            for k, v in message.labels.items()
            if k != "task_name"
        ):
            return f"{message.task_name}[{labels_str}]"

        else:
            return message.task_name

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        # NOTE: Ensure we always have this, no matter what
        message.labels["task_name"] = message.task_name

        if "task_type" not in message.labels:
            return message  # Not a silverback task

        task_type_str = message.labels.pop("task_type")

        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            return message  # Not a silverback task

        # Add extra labels for our task to see what their source was
        if task_type is TaskType.NEW_BLOCK:
            # NOTE: Necessary because we don't know the exact block class
            block = message.args[0] = self.provider.network.ecosystem.decode_block(
                hexbytes_dict(message.args[0])
            )
            message.labels["block_number"] = str(block.number)
            message.labels["block_hash"] = block.hash.hex()

        elif task_type is TaskType.EVENT_LOG:
            # NOTE: Just in case the user doesn't specify type as `ContractLog`
            log = message.args[0] = ContractLog.model_validate(message.args[0])
            message.labels["block_number"] = str(log.block_number)
            message.labels["transaction_hash"] = log.transaction_hash
            message.labels["log_index"] = str(log.log_index)

        logger.info(f"{self._create_label(message)} - Started")
        return message

    def post_execute(self, message: TaskiqMessage, result: TaskiqResult):
        if self.block_time:
            percentage_time = 100 * (result.execution_time / self.block_time)
            percent_display = f" ({percentage_time:.1f}%)"

        else:
            percent_display = ""

        msg = f"{self._create_label(message)} " f"- {result.execution_time:.3f}s{percent_display}"
        if result.is_err:
            logger.error(msg)
        elif message.task_name.startswith("system:"):
            logger.debug(msg)
        else:
            logger.success(msg)

    # NOTE: Unless stdout is ignored, error traceback appears in stdout, no need for `on_error`
