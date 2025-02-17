/*
 * oSPARC - The SIMCORE frontend - https://osparc.io
 * Copyright: 2019 IT'IS Foundation - https://itis.swiss
 * License: MIT - https://opensource.org/licenses/MIT
 * Authors: Ignacio Pascual (ignapas)
 */

qx.Class.define("osparc.ui.basic.NodeStatusUI", {
  extend: qx.ui.basic.Atom,

  construct: function(node) {
    this.base(arguments, this.tr("Idle"), "@FontAwesome5Solid/clock/12");

    this.__label = this.getChildControl("label");
    this.__icon = this.getChildControl("icon");

    this.__setupBlank();
    if (node) {
      this.setNode(node);
    }
  },

  properties: {
    appearance: {
      init: "chip",
      refine: true
    },

    node: {
      check: "osparc.data.model.Node",
      apply: "__applyNode",
      nullable: false,
      init: null
    }
  },

  members: {
    __node: null,
    __label: null,
    __icon: null,

    __applyNode: function(node) {
      this.show();
      if (node.isFilePicker()) {
        this.__setupFilePicker();
      } else if (node.isComputational()) {
        this.__setupComputational();
      } else if (node.isDynamic()) {
        this.__setupInteractive();
      } else {
        this.__setupBlank();
      }
      node.bind("errors", this, "toolTipText", {
        converter: errors => {
          let errorsText = "";
          if (errors) {
            errors.forEach(error => errorsText += error["msg"] + "<br>");
          }
          return errorsText;
        }
      });
    },

    __setupComputational: function() {
      this.getNode().getStatus().bind("running", this.__label, "value", {
        converter: state => {
          if (state) {
            this.show();
            const labelValue = osparc.utils.StatusUI.getLabelValue(state);
            return qx.lang.String.firstUp(labelValue.toLowerCase());
          }
          this.exclude();
          return null;
        },
        onUpdate: (source, target) => {
          const state = source.getRunning();
          target.setTextColor(osparc.utils.StatusUI.getColor(state));
        }
      });

      this.getNode().getStatus().bind("running", this.__icon, "source", {
        converter: state => osparc.utils.StatusUI.getIconSource(state),
        onUpdate: (source, target) => {
          target.show();
          const state = source.getRunning();
          switch (state) {
            case "SUCCESS":
            case "FAILED":
            case "ABORTED":
              osparc.utils.Utils.removeClass(this.__icon.getContentElement(), "rotate");
              target.setTextColor(osparc.utils.StatusUI.getColor(state));
              return;
            case "PENDING":
            case "PUBLISHED":
            case "STARTED":
            case "RETRY":
              osparc.utils.Utils.addClass(this.__icon.getContentElement(), "rotate");
              target.setTextColor(osparc.utils.StatusUI.getColor(state));
              return;
            case "UNKNOWN":
            case "NOT_STARTED":
            default:
              target.exclude();
              return;
          }
        }
      });
    },

    __setupInteractive: function() {
      this.getNode().getStatus().bind("interactive", this.__label, "value", {
        converter: state => osparc.utils.StatusUI.getLabelValue(state),
        onUpdate: (source, target) => {
          const state = source.getInteractive();
          target.setTextColor(osparc.utils.StatusUI.getColor(state));
        }
      });

      this.getNode().getStatus().bind("interactive", this.__icon, "source", {
        converter: state => osparc.utils.StatusUI.getIconSource(state),
        onUpdate: (source, target) => {
          osparc.utils.StatusUI.updateIconAnimation(this.__icon);
          const props = qx.util.PropertyUtil.getProperties(osparc.data.model.NodeStatus);
          const state = source.getInteractive();
          if (props["interactive"]["check"].includes(state)) {
            target.setTextColor(osparc.utils.StatusUI.getColor(state));
          } else {
            target.resetTextColor();
          }
        }
      });
    },

    __setupFilePicker: function() {
      osparc.utils.StatusUI.setupFilePickerIcon(this.getNode(), this.__icon);

      this.getNode().bind("outputs", this.__label, "value", {
        converter: outputs => {
          if (osparc.file.FilePicker.getOutput(outputs)) {
            let outputLabel = osparc.file.FilePicker.getOutputLabel(outputs);
            if (outputLabel === "" && osparc.file.FilePicker.isOutputDownloadLink(outputs)) {
              outputLabel = osparc.file.FilePicker.extractLabelFromLink(outputs);
            }
            return outputLabel;
          }
          return this.tr("Select a file");
        }
      });

      this.getNode().getStatus().addListener("changeProgress", e => {
        const progress = e.getData();
        if (progress > 0 && progress < 100) {
          this.__label.setValue(this.tr("Uploading"));
        }
      });
    },

    __setupBlank: function() {
      this.exclude();
    }
  }
});
