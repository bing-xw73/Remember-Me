# SPDX-License-Identifier: CPAL-1.0
"""Build an inspectable, token-free Apple Shortcuts plist; signing needs macOS."""

import argparse
from pathlib import Path
import plistlib
import uuid


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "remember-me/shortcut/" + name)).upper()


def ref(name):
    return {"WFSerializationType": "WFTextTokenAttachment", "Value": {
        "Type": "ActionOutput", "OutputUUID": uid(name), "OutputName": name}}


def text(value):
    return {"WFSerializationType": "WFTextTokenString", "Value": {"string": value}}


def token_text(name, prefix=""):
    return {"WFSerializationType": "WFTextTokenString", "Value": {
        "string": prefix + "\ufffc", "attachmentsByRange": {
            "{%d, 1}" % len(prefix): ref(name)["Value"]}}}


def dictionary(items):
    return {"WFSerializationType": "WFDictionaryFieldValue", "Value": {
        "WFDictionaryFieldValueItems": [
            {"WFItemType": kind, "WFKey": text(key), "WFValue": value}
            for key, kind, value in items]}}


def build():
    actions = []
    def action(name, identifier, **params):
        actions.append({"WFWorkflowActionIdentifier": "is.workflow.actions." + identifier,
                        "WFWorkflowActionParameters": {"UUID": uid(name), "CustomOutputName": name, **params}})
    action("License", "comment", WFCommentActionText="SPDX-License-Identifier: CPAL-1.0\nRemember-Me — originally created by Ting (peanutsuee).\nTemplate only: replace the upload-only token after importing; never use the main token.")
    action("Upload Token", "gettext", WFTextActionText="REPLACE_WITH_UPLOAD_ONLY_TOKEN")
    shortcut_input = {"WFSerializationType": "WFTextTokenAttachment", "Value": {"Type": "ExtensionInput"}}
    action("Photo", "getitemfromlist", WFInput=shortcut_input, WFItemSpecifier="First Item")
    action("Width", "properties.images", WFInput=ref("Photo"), WFContentItemPropertyName="Width")
    action("Height", "properties.images", WFInput=ref("Photo"), WFContentItemPropertyName="Height")
    group = uid("orientation")
    action("Orientation", "conditional", WFInput=ref("Width"), WFCondition=4,
           WFNumberValue=ref("Height"), WFControlFlowMode=0, GroupingIdentifier=group)
    action("Landscape", "image.resize", WFInput=ref("Photo"), WFImageResizeWidth=2048)
    action("Otherwise", "conditional", WFControlFlowMode=1, GroupingIdentifier=group)
    action("Portrait", "image.resize", WFInput=ref("Photo"), WFImageResizeWidth="", WFImageResizeHeight=2048)
    action("Resized", "conditional", WFControlFlowMode=2, GroupingIdentifier=group)
    action("JPEG", "image.convert", WFInput=ref("Resized"), WFImageFormat="JPEG",
           WFImageCompressionQuality=0.85, WFImagePreserveMetadata=False)
    action("Description", "ask", WFAskActionPrompt="这张图是什么？（可以留空）", WFInputType="Text", WFAskActionDefaultAnswer="")
    file_value = {"WFSerializationType": "WFTokenAttachmentParameterState", "Value": ref("JPEG")}
    action("Upload", "downloadurl", WFURL="https://rm.wu-bing.xyz/api/v1/quick-upload",
           Advanced=True, ShowHeaders=True, WFHTTPMethod="POST", WFHTTPBodyType="Form",
           WFRequestVariable=ref("JPEG"), WFHTTPHeaders=dictionary([
               ("Authorization", 0, token_text("Upload Token", "Bearer "))]),
           WFFormValues=dictionary([("file", 5, file_value), ("description", 0, token_text("Description")), ("tags", 0, text(""))]))
    action("Asset ID", "getvalueforkey", WFInput=ref("Upload"), WFDictionaryKey="asset_id", WFGetDictionaryValueType="Value")
    group = uid("success")
    action("Success", "conditional", WFInput=ref("Asset ID"), WFCondition=100, WFControlFlowMode=0, GroupingIdentifier=group)
    action("Saved", "notification", WFNotificationActionTitle="Remember-Me", WFNotificationActionBody="已存入")
    action("Failure", "conditional", WFControlFlowMode=1, GroupingIdentifier=group)
    action("Error", "alert", WFAlertActionTitle="未存入", WFAlertActionMessage=token_text("Upload"))
    action("End", "conditional", WFControlFlowMode=2, GroupingIdentifier=group)
    return {"WFWorkflowActions": actions, "WFWorkflowName": "存进 Remember-Me",
            "WFWorkflowClientVersion": "3036.0.4", "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowTypes": ["ActionExtension"], "WFWorkflowInputContentItemClasses": ["WFImageContentItem"],
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59511},
            "WFImportQuestions": [{"ActionIndex": 1, "Category": "Parameter", "ParameterKey": "WFTextActionText",
                                   "Text": "填入 Remember-Me 上传专用 Token（不是主 Token）", "DefaultValue": "REPLACE_WITH_UPLOAD_ONLY_TOKEN"}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("shortcuts/Remember-Me.unsigned.shortcut"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = plistlib.dumps(build(), fmt=plistlib.FMT_XML, sort_keys=False)
    source = source.replace(b"\n", b"\n<!-- SPDX-License-Identifier: CPAL-1.0 -->\n", 1)
    args.output.write_bytes(source)
    print("Unsigned shortcut source written; sign on macOS before importing on iPhone.")


if __name__ == "__main__":
    main()
