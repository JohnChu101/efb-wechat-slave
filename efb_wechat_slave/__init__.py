# coding: utf-8

import io
import json
import logging
import os
import tempfile
import threading
from gettext import translation
from json import JSONDecodeError
from tempfile import NamedTemporaryFile
from typing import IO, Any, Dict, Optional, List, Tuple, Callable
from uuid import uuid4

import yaml
from PIL import Image
from pkg_resources import resource_filename
from pyqrcode import QRCode
from typing_extensions import Final

from ehforwarderbot import EFBChannel, EFBMsg, MsgType, ChannelType, \
    ChatType, EFBStatus, EFBChat, coordinator
from ehforwarderbot import utils as efb_utils
from ehforwarderbot.exceptions import EFBMessageTypeNotSupported, EFBMessageError, EFBChatNotFound, \
    EFBOperationNotSupported
from ehforwarderbot.message import EFBMsgCommands, EFBMsgCommand
from ehforwarderbot.status import EFBMessageRemoval
from ehforwarderbot.types import MessageID
from ehforwarderbot.utils import extra
from . import utils as ews_utils
from .__version__ import __version__
from .chats import ChatManager
from .slave_message import SlaveMessageManager
from .utils import ExperimentalFlagsManager
from .vendor import wxpy
from .vendor.wxpy import ResponseError
from .vendor.wxpy.utils import PuidMap


class WeChatChannel(EFBChannel):
    """
    EFB Channel - WeChat Slave Channel
    Based on wxpy (itchat), WeChat Web Client

    Author: Eana Hufwe <https://github.com/blueset>
    """

    channel_name = "WeChat Slave"
    channel_emoji = "💬"
    channel_id = 'blueset.wechat'
    channel_type = ChannelType.Slave

    __version__ = __version__

    supported_message_types = {MsgType.Text, MsgType.Sticker, MsgType.Image,
                               MsgType.File, MsgType.Video, MsgType.Link, MsgType.Audio,
                               MsgType.Animation}
    logger: logging.Logger = logging.getLogger("plugins.%s.WeChatChannel" % channel_id)
    qr_uuid: Tuple[str, int] = ('', 0)
    done_reauth: threading.Event = threading.Event()
    _stop_polling_event: threading.Event = threading.Event()

    config: Dict[str, Any] = dict()

    bot: wxpy.Bot

    # GNU Gettext Translator

    translator = translation("efb_wechat_slave",
                             resource_filename('efb_wechat_slave', 'locale'),
                             fallback=True)

    _: Callable = translator.gettext
    ngettext: Callable = translator.ngettext

    SYSTEM_ACCOUNTS: Final = {
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'filehelper': _('filehelper'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'newsapp': _('newsapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'fmessage': _('fmessage'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'weibo': _('weibo'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'qqmail': _('qqmail'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'tmessage': _('tmessage'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'qmessage': _('qmessage'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'qqsync': _('qqsync'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'floatbottle': _('floatbottle'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'lbsapp': _('lbsapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'shakeapp': _('shakeapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'medianote': _('medianote'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'qqfriend': _('qqfriend'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'readerapp': _('readerapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'blogapp': _('blogapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'facebookapp': _('facebookapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'masssendapp': _('masssendapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'meishiapp': _('meishiapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'feedsapp': _('feedsapp'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'voip': _('voip'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'blogappweixin': _('blogappweixin'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'weixin': _('weixin'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'brandsessionholder': _('brandsessionholder'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'weixinreminder': _('weixinreminder'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'officialaccounts': _('officialaccounts'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'notification_messages': _('notification_messages'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'wxitil': _('wxitil'),
        # TRANSLATORS: Translate this to the corresponding display name of the WeChat system account. Guessed names are not accepted.
        'userexperience_alarm': _('userexperience_alarm'),
    }

    # Constants
    MAX_FILE_SIZE: int = 5 * 2 ** 20

    def __init__(self, instance_id: str = None):
        """
        Initialize the channel

        Args:
            coordinator (:obj:`ehforwarderbot.coordinator.EFBCoordinator`):
                The EFB framework coordinator
        """
        super().__init__(instance_id)
        self.load_config()

        PuidMap.SYSTEM_ACCOUNTS = self.SYSTEM_ACCOUNTS

        self.flag: ExperimentalFlagsManager = ExperimentalFlagsManager(self)

        self.authenticate('console_qr_code')

        # Managers
        self.slave_message: SlaveMessageManager = SlaveMessageManager(self)
        self.chats: ChatManager = ChatManager(self)

    def load_config(self):
        """
        Load configuration from path specified by the framework.

        Configuration file is in YAML format.
        """
        config_path = efb_utils.get_config_path(self.channel_id)
        if not config_path.exists():
            return
        with config_path.open() as f:
            d = yaml.load(f)
            if not d:
                return
            self.config: Dict[str, Any] = d

    #
    # Utilities
    #

    def console_qr_code(self, uuid, status, qrcode=None):
        status = int(status)
        if self.qr_uuid == (uuid, status):
            return
        self.qr_uuid = (uuid, status)
        if status == 201:
            qr = self._('Confirm on your phone.')
            return self.logger.log(99, qr)
        elif status == 200:
            qr = self._("Successfully logged in.")
            return self.logger.log(99, qr)
        else:
            # 0: First QR code
            # 408: Updated QR code
            qr = self._("EWS: Please scan the QR code with your camera, screenshots will not work. ({0}, {1})") \
                     .format(uuid, status) + "\n"
            if status == 408:
                qr += self._("QR code expired, please scan the new one.") + "\n"
            qr += "\n"
            qr_url = "https://login.weixin.qq.com/l/" + uuid
            qr_obj = QRCode(qr_url)
            if self.flag("imgcat_qr"):
                qr_file = io.BytesIO()
                qr_obj.png(qr_file, scale=10)
                qr_file.seek(0)
                qr += ews_utils.imgcat(qr_file, f"{self.channel_id}_QR_{uuid}.png")
            else:
                qr += qr_obj.terminal()
            qr += "\n" + self._("If the QR code was not shown correctly, please visit:\n"
                                "https://login.weixin.qq.com/qrcode/{0}").format(uuid)
            return self.logger.log(99, qr)

    def master_qr_code(self, uuid, status, qrcode=None):
        status = int(status)
        if self.qr_uuid == (uuid, status):
            return
        self.qr_uuid = (uuid, status)

        msg = EFBMsg()
        msg.uid = f"ews_auth_{uuid}_{status}"
        msg.type = MsgType.Text
        msg.chat = EFBChat(self).system()
        msg.chat.chat_name = self._("EWS User Auth")
        msg.author = msg.chat
        msg.deliver_to = coordinator.master

        if status == 201:
            msg.type = MsgType.Text
            msg.text = self._('Confirm on your phone.')
        elif status == 200:
            msg.type = MsgType.Text
            msg.text = self._("Successfully logged in.")
        elif uuid != self.qr_uuid:
            msg.type = MsgType.Image
            file = NamedTemporaryFile(suffix=".png")
            qr_url = "https://login.weixin.qq.com/l/" + uuid
            QRCode(qr_url).png(file, scale=10)
            msg.text = self._("QR code expired, please scan the new one.")
            msg.path = file.name
            msg.file = file
            msg.mime = 'image/png'
        if status in (200, 201) or uuid != self.qr_uuid:
            coordinator.send_message(msg)

    def exit_callback(self):
        # Don't send prompt if there's nowhere to send.
        if not getattr(coordinator, 'master', None):
            raise Exception(self._("Web WeChat logged your account out before master channel is ready."))
        self.logger.debug('Calling exit callback...')
        if self._stop_polling_event.is_set():
            return
        msg = EFBMsg()
        chat = EFBChat(self).system()
        chat.chat_type = ChatType.System
        chat.chat_name = self._("EWS User Auth")
        msg.chat = msg.author = chat
        msg.deliver_to = coordinator.master
        msg.text = self._("WeChat server has logged you out. Please log in again when you are ready.")
        msg.uid = f"__reauth__.{uuid4()}"
        msg.type = MsgType.Text
        on_log_out = self.flag("on_log_out")
        on_log_out = on_log_out if on_log_out in ("command", "idle", "reauth") else "command"
        if on_log_out == "command":
            msg.type = MsgType.Text
            msg.commands = EFBMsgCommands(
                [EFBMsgCommand(name=self._("Log in again"), callable_name="reauth", kwargs={"command": True})])
        elif on_log_out == "reauth":
            if self.flag("qr_reload") == "console_qr_code":
                msg.text += "\n" + self._("Please check your log to continue.")
            self.reauth()

        coordinator.send_message(msg)

    def poll(self):
        self.bot.start()
        self._stop_polling_event.wait()
        # while not self.stop_polling:
        #     if not self.bot.alive:
        #         self.done_reauth.wait()
        #         self.done_reauth.clear()
        self.logger.debug("%s (%s) gracefully stopped.", self.channel_name, self.channel_id)

    def send_message(self, msg: EFBMsg) -> EFBMsg:
        """Send a message to WeChat.
        Supports text, image, sticker, and file.

        Args:
            msg (channel.EFBMsg): Message Object to be sent.

        Returns:
            This method returns nothing.

        Raises:
            EFBMessageTypeNotSupported: Raised when message type is not supported by the channel.
        """
        chat: wxpy.Chat = self.chats.get_wxpy_chat_by_uid(msg.chat.chat_uid)

        # List of "SentMessage" response for all messages sent
        r: List[wxpy.SentMessage] = []
        self.logger.info("[%s] Sending message to WeChat:\n"
                         "uid: %s\n"
                         "UserName: %s\n"
                         "NickName: %s\n"
                         "Type: %s\n"
                         "Text: %s",
                         msg.uid,
                         msg.chat.chat_uid, chat.user_name, chat.name, msg.type, msg.text)

        try:
            chat.mark_as_read()
        except wxpy.ResponseError as e:
            self.logger.exception("[%s] Error occurred while marking chat as read. (%s)", msg.uid, e)

        send_text_only = False
        self.logger.debug('[%s] Is edited: %s', msg.uid, msg.edit)
        if msg.edit:
            if self.flag('delete_on_edit'):
                msg_ids = json.loads(msg.uid)
                if not msg.edit_media:
                    # Treat message as text message to prevent resend of media
                    msg_ids = msg_ids[1:]
                    send_text_only = True
                failed = 0
                for i in msg_ids:
                    try:
                        ews_utils.message_to_dummy_message(i, self).recall()
                    except wxpy.ResponseError as e:
                        self.logger.error("[%s] Trying to recall message but failed: %s", msg.uid, e)
                        failed += 1
                if failed:
                    raise EFBMessageError(
                        self.ngettext('Failed to recall {failed} out of {total} message, edited message was not sent.',
                                      'Failed to recall {failed} out of {total} messages, edited message was not sent.',
                                      len(msg_ids)).format(
                            failed=failed,
                            total=len(msg_ids)
                        ))
                # Not caching message ID as message recall feedback is not needed in edit mode
            else:
                raise EFBOperationNotSupported()
        if send_text_only or msg.type in [MsgType.Text, MsgType.Link]:
            if isinstance(msg.target, EFBMsg):
                max_length = self.flag("max_quote_length")
                qt_txt = msg.target.text or msg.target.type.name
                if max_length > 0:
                    if len(qt_txt) >= max_length:
                        tgt_text = qt_txt[:max_length]
                        tgt_text += "…"
                    else:
                        tgt_text = qt_txt
                elif max_length < 0:
                    tgt_text = qt_txt
                else:
                    tgt_text = ""
                if isinstance(chat, wxpy.Group) and not msg.target.author.is_self:
                    tgt_alias = "@%s\u2005：" % msg.target.author.display_name
                else:
                    tgt_alias = ""
                msg.text = f"「{tgt_alias}{tgt_text}」\n- - - - - - - - - - - - - - -\n{msg.text}"
            r.append(self._bot_send_msg(chat, msg.text))
            self.logger.debug('[%s] Sent as a text message. %s', msg.uid, msg.text)
        elif msg.type in (MsgType.Image, MsgType.Sticker, MsgType.Animation):
            self.logger.info("[%s] Image/GIF/Sticker %s", msg.uid, msg.type)

            convert_to = None
            file = msg.file
            assert file is not None

            if self.flag('send_stickers_and_gif_as_jpeg'):
                if msg.type == MsgType.Sticker or msg.mime == "image/gif":
                    convert_to = "image/jpeg"
            else:
                if msg.type == MsgType.Sticker:
                    convert_to = "image/gif"

            if convert_to == "image/gif":
                with NamedTemporaryFile(suffix=".gif") as f:
                    try:
                        img = Image.open(file)
                        try:
                            alpha = img.split()[3]
                            mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
                        except IndexError:
                            mask = Image.eval(img.split()[0], lambda a: 0)
                        img = img.convert('RGB').convert('P', palette=Image.ADAPTIVE, colors=255)
                        img.paste(255, mask)
                        img.save(f, transparency=255)
                        msg.path = f.name
                        self.logger.debug('[%s] Image converted from %s to GIF', msg.uid, msg.mime)
                        file.close()
                        f.seek(0)
                        if os.fstat(f.fileno()).st_size > self.MAX_FILE_SIZE:
                            raise EFBMessageError(self._("Image size is too large. (IS02)"))
                        r.append(self._bot_send_image(chat, f.name, f))
                    finally:
                        if not file.closed:
                            file.close()
            elif convert_to == "image/jpeg":
                with NamedTemporaryFile(suffix=".jpg") as f:
                    try:
                        img = Image.open(file).convert('RGBA')
                        out = Image.new("RGBA", img.size, (255, 255, 255, 255))
                        out.paste(img, img)
                        out.convert('RGB').save(f)
                        msg.path = f.name
                        self.logger.debug('[%s] Image converted from %s to JPEG', msg.uid, msg.mime)
                        file.close()
                        f.seek(0)
                        if os.fstat(f.fileno()).st_size > self.MAX_FILE_SIZE:
                            raise EFBMessageError(self._("Image size is too large. (IS02)"))
                        r.append(self._bot_send_image(chat, f.name, f))
                    finally:
                        if not file.closed:
                            file.close()
            else:
                try:
                    if os.fstat(file.fileno()).st_size > self.MAX_FILE_SIZE:
                        raise EFBMessageError(self._("Image size is too large. (IS01)"))
                    self.logger.debug("[%s] Sending %s (image) to WeChat.", msg.uid, msg.path)
                    r.append(self._bot_send_image(chat, msg.path, file))
                finally:
                    if not file.closed:
                        file.close()
            if msg.text:
                r.append(self._bot_send_msg(chat, msg.text))
        elif msg.type in (MsgType.File, MsgType.Audio):
            self.logger.info("[%s] Sending %s to WeChat\nFileName: %s\nPath: %s\nFilename: %s",
                             msg.uid, msg.type, msg.text, msg.path, msg.filename)
            r.append(self._bot_send_file(chat, msg.filename, file=msg.file))
            if msg.text:
                self._bot_send_msg(chat, msg.text)
            msg.file.close()
        elif msg.type == MsgType.Video:
            self.logger.info("[%s] Sending video to WeChat\nFileName: %s\nPath: %s", msg.uid, msg.text, msg.path)
            r.append(self._bot_send_video(chat, msg.path, file=msg.file))
            if msg.text:
                r.append(self._bot_send_msg(chat, msg.text))
            msg.file.close()
        else:
            raise EFBMessageTypeNotSupported()

        msg.uid = ews_utils.generate_message_uid(r)
        self.logger.debug('WeChat message is assigned with unique ID: %s', msg.uid)
        return msg

    def send_status(self, status: EFBStatus):
        if isinstance(status, EFBMessageRemoval):
            if not status.message.author.is_self:
                raise EFBOperationNotSupported(self._('You can only recall your own messages.'))
            try:
                msg_ids = json.loads(status.message.uid)
            except JSONDecodeError:
                raise EFBMessageError(self._("ID of the message to recall is invalid."))
            failed = 0
            if any(len(i) == 1 for i in msg_ids):  # Message is not sent through EWS
                raise EFBOperationNotSupported(
                    self._("You may only recall messages sent via EWS.")
                )
            for i in msg_ids:
                try:
                    ews_utils.message_to_dummy_message(i, self).recall()
                except wxpy.ResponseError:
                    failed += 1
            if failed:
                raise EFBMessageError(
                    self.ngettext(
                        'Failed to recall {failed} of {total} message.',
                        'Failed to recall {failed} of {total} messages.',
                        len(msg_ids)
                    ).format(failed=failed, total=len(msg_ids)))
            else:
                val = [status.message.uid, len(msg_ids)]
                for i in msg_ids:
                    self.slave_message.recall_msg_id_conversion[str(i[1])] = val
        else:
            raise EFBOperationNotSupported()

    def get_chat_picture(self, chat: EFBChat) -> IO[bytes]:
        uid = chat.chat_uid
        if uid in wxpy.Chat.SYSTEM_ACCOUNTS:
            wxpy_chat: wxpy.Chat = wxpy.Chat(wxpy.utils.wrap_user_name(uid), self.bot)
        else:
            wxpy_chat = wxpy.utils.ensure_one(self.bot.search(puid=uid))
        f = None
        try:
            f = tempfile.NamedTemporaryFile(suffix='.jpg')
            data = wxpy_chat.get_avatar(None)
            if not data:
                raise EFBOperationNotSupported()
            f.write(data)
            f.seek(0)
            return f
        except (TypeError, ResponseError):
            if f is not None:
                f.close()
            raise EFBOperationNotSupported()

    # Additional features

    @extra(name=_("Show chat list"),
           desc=_("Show a list of chats from WeChat.\n"
                  "Usage:\n    {function_name} [-r]\n"
                  "    -r: Refresh list"))
    def get_chat_list(self, param: str = "") -> str:
        refresh = False
        if param:
            if param == "-r":
                refresh = True
            else:
                return self._("Unknown parameter: {}.").format(param)
        l: List[wxpy.Chat] = self.bot.chats(refresh)

        msg = self._("Chat list:") + "\n"
        for i in l:
            alias = ews_utils.wechat_string_unescape(getattr(i, 'remark_name', '') or
                                                     getattr(i, 'display_name', ''))
            name = ews_utils.wechat_string_unescape(i.nick_name)
            display_name = "%s (%s)" % (alias, name) if alias and alias != name else name
            chat_type = "?"
            if isinstance(i, wxpy.MP):
                # TRANSLATORS: Acronym for MP accounts
                chat_type = self._('MP')
            elif isinstance(i, wxpy.Group):
                # TRANSLATORS: Acronym for groups
                chat_type = self._('Gr')
            elif isinstance(i, wxpy.User):
                # TRANSLATORS: Acronym for users/friends
                chat_type = self._('Fr')
            msg += "\n%s: [%s] %s" % (i.puid, chat_type, display_name)

        return msg

    @extra(name=_("Set alias"),
           desc=_("Set an alias (remark name) for friends. Not applicable to "
                  "groups and MPs.\n"
                  "Usage:\n"
                  "    {function_name} id [alias]\n"
                  "    id: Chat ID, available from \"Show chat list\".\n"
                  "    alias: Alias. Leave empty to delete alias."))
    def set_alias(self, r_param: str = "") -> str:
        if r_param:
            param = r_param.split(maxsplit=1)
            if len(param) == 1:
                cid = param[0]
                alias = ""
            else:
                cid, alias = param
        else:
            return self.set_alias.desc

        chat = self.bot.search(cid)

        if not chat:
            return self._("Chat {0} is not found.").format(cid)

        if not isinstance(chat, wxpy.User):
            return self._("Remark name is only applicable to friends.")

        chat.set_remark_name(alias)

        if alias:
            return self._("\"{0}\" now has remark name \"{1}\".").format(chat.nick_name, alias)
        else:
            return self._("Remark name of \"{0}\" has been removed.").format(chat.nick_name)

    @extra(name=_("Log out"),
           desc=_("Log out from WeChat and try to log in again.\n"
                  "Usage: {function_name}"))
    def force_log_out(self, _: str = "") -> str:
        self.bot.logout()
        self.exit_callback()
        return self._("Done.")

    # Command functions

    def reauth(self, command=False):
        msg = self._("Preparing to log in...")
        qr_reload = self.flag("qr_reload")
        if command and qr_reload == "console_qr_code":
            msg += "\n" + self._("Please check your log to continue.")

        threading.Thread(target=self.authenticate, args=(qr_reload,), name="EWS reauth thread").start()
        return msg

    def authenticate(self, qr_reload, first_start=False):
        qr_callback = getattr(self, qr_reload, self.master_qr_code)
        if getattr(self, 'bot', None):  # if a bot exists
            self.bot.cleanup()
        with coordinator.mutex:
            self.bot: wxpy.Bot = wxpy.Bot(cache_path=str(efb_utils.get_data_path(self.channel_id) / "wxpy.pkl"),
                                          qr_callback=qr_callback,
                                          logout_callback=self.exit_callback,
                                          user_agent=self.flag('user_agent'),
                                          start_immediately=not first_start)
            self.bot.enable_puid(
                efb_utils.get_data_path(self.channel_id) / "wxpy_puid.pkl",
                self.flag('puid_logs')
            )
            self.done_reauth.set()
            if hasattr(self, "slave_message"):
                self.slave_message.bot = self.bot
                self.slave_message.wechat_msg_register()

    def add_friend(self, username: str = None, verify_information: str = "") -> str:
        if not username:
            return self._("Empty username (UE02).")
        try:
            self.bot.add_friend(user=username, verify_content=verify_information)
        except wxpy.ResponseError as r:
            return self._("Error occurred while processing (AF01).") + "\n\n{}: {!r}".format(r.err_code, r.err_msg)
        return self._("Request sent.")

    def accept_friend(self, username: str = None, verify_information: str = "") -> str:
        if not username:
            return self._("Empty username (UE03).")
        try:
            self.bot.accept_friend(user=username, verify_content=verify_information)
        except wxpy.ResponseError as r:
            return self._("Error occurred while processing (AF02).") + "n\n{}: {!r}".format(r.err_code, r.err_msg)
        return self._("Request accepted.")

    def get_chats(self) -> List[EFBChat]:
        """
        Get all chats available from WeChat
        """
        return self.chats.get_chats()

    def get_chat(self, chat_uid: str, member_uid: Optional[str] = None) -> EFBChat:
        if member_uid:
            chat = self.chats.search_member(uid=chat_uid, member_id=member_uid)
            if not chat:
                raise EFBChatNotFound()
            else:
                return chat
        else:
            chat = self.chats.search_chat(uid=chat_uid)
            if not chat:
                raise EFBChatNotFound()
            else:
                return chat

    def stop_polling(self):
        self.bot.cleanup()
        if not self._stop_polling_event.is_set():
            self._stop_polling_event.set()
        else:
            self.done_reauth.set()

    def _bot_send_msg(self, chat: wxpy.Chat, message: str) -> wxpy.SentMessage:
        try:
            return chat.send_msg(message)
        except wxpy.ResponseError as e:
            e = self.substitute_known_error_reason(e)
            raise EFBMessageError(self._("Error from Web WeChat while sending message: [{code}] {message}")
                                  .format(code=e.err_code, message=e.err_msg))

    def _bot_send_file(self, chat: wxpy.Chat, filename: str, file: IO[bytes]) -> wxpy.SentMessage:
        try:
            return chat.send_file(filename, file=file)
        except wxpy.ResponseError as e:
            e = self.substitute_known_error_reason(e)
            raise EFBMessageError(self._("Error from Web WeChat while sending file: [{code}] {message}")
                                  .format(code=e.err_code, message=e.err_msg))

    def _bot_send_image(self, chat: wxpy.Chat, filename: str, file: IO[bytes]) -> wxpy.SentMessage:
        try:
            return chat.send_image(filename, file=file)
        except wxpy.ResponseError as e:
            e = self.substitute_known_error_reason(e)
            raise EFBMessageError(self._("Error from Web WeChat while sending image: [{code}] {message}")
                                  .format(code=e.err_code, message=e.err_msg))

    def _bot_send_video(self, chat: wxpy.Chat, filename: str, file: IO[bytes]) -> wxpy.SentMessage:
        try:
            return chat.send_video(filename, file=file)
        except wxpy.ResponseError as e:
            e = self.substitute_known_error_reason(e)
            raise EFBMessageError(self._("Error from Web WeChat while sending video: [{code}] {message}")
                                  .format(code=e.err_code, message=e.err_msg))

    def substitute_known_error_reason(self, err: wxpy.ResponseError) -> wxpy.ResponseError:
        if not err.err_msg:
            issue_url = "https://ews.1a23.studio/issues/55"
            if err.err_code in (1101, 1102, 1103):
                err.err_msg = self._("Your Web WeChat session might be expired. "
                                     "Please try to log out with the “force_log_out” command, and log in again. "
                                     "If you believe that is not the case, please leave a comment at {issue_url} .").format(
                    issue_url=issue_url
                )
            elif err.err_code == 1204:
                err.err_msg = self._("You don’t have access to the chat that you are trying to send message to.")
            elif err.err_code == 1205:
                err.err_msg = self._("You might have sent your messages too fast. Please try to slow down "
                                     "and retry after a while.")
            else:
                err.err_msg = self._("This is an unknown error from Web WeChat which we know nothing about why this "
                                     "is happening. If you have seen a pattern or if you happen to know the reason "
                                     "for this error code, please leave a comment at {issue_url} .").format(
                    issue_url=issue_url
                )
        return err

    def get_message_by_id(self, chat: EFBChat, msg_id: MessageID) -> Optional['EFBMsg']:
        raise EFBOperationNotSupported()
