/* ************************************************************************

   osparc - the simcore frontend

   https://osparc.io

   Copyright:
     2018 IT'IS Foundation, https://itis.swiss

   License:
     MIT: https://opensource.org/licenses/MIT

   Authors:
     * Odei Maiz (odeimaiz)

************************************************************************ */

qx.Class.define("osparc.desktop.StudyEditor", {
  extend: osparc.ui.basic.LoadingPageHandler,

  construct: function() {
    this.base(arguments);

    this._setLayout(new qx.ui.layout.VBox(10));

    const viewsStack = this.__viewsStack = new qx.ui.container.Stack();

    const workbenchView = this.__workbenchView = new osparc.desktop.WorkbenchView();
    [
      "collapseNavBar",
      "expandNavBar",
      "backToDashboardPressed",
      "slidesEdit",
      "slidesAppStart"
    ].forEach(singalName => workbenchView.addListener(singalName, () => this.fireEvent(singalName)));
    workbenchView.addListener("takeSnapshot", () => this.__takeSnapshot(), this);
    workbenchView.addListener("showSnapshots", () => this.__showSnapshots(), this);
    workbenchView.addListener("createIterations", () => this.__createIterations(), this);
    workbenchView.addListener("showIterations", () => this.__showIterations(), this);
    workbenchView.addListener("changeSelectedNode", e => {
      if (this.__nodesSlidesTree) {
        const nodeId = e.getData();
        this.__nodesSlidesTree.changeSelectedNode(nodeId);
      }
    });
    viewsStack.add(workbenchView);

    const slideshowView = this.__slideshowView = new osparc.desktop.SlideshowView();
    [
      "collapseNavBar",
      "expandNavBar",
      "backToDashboardPressed",
      "slidesStop"
    ].forEach(singalName => slideshowView.addListener(singalName, () => this.fireEvent(singalName)));
    viewsStack.add(slideshowView);

    const wbAppear = new Promise(resolve => workbenchView.addListenerOnce("appear", resolve, false));
    const ssAppear = new Promise(resolve => slideshowView.addListenerOnce("appear", resolve, false));
    Promise.all([wbAppear, ssAppear]).then(() => {
      // both are ready
      workbenchView.getCollapseWithUserMenu().bind("collapsed", slideshowView.getCollapseWithUserMenu(), "collapsed");
      slideshowView.getCollapseWithUserMenu().bind("collapsed", workbenchView.getCollapseWithUserMenu(), "collapsed");
    });

    slideshowView.addListener("startPartialPipeline", e => {
      const partialPipeline = e.getData();
      this.__startPipeline(partialPipeline);
    }, this);
    slideshowView.addListener("stopPipeline", this.__stopPipeline, this);

    [
      workbenchView.getStartStopButtons()
    ].forEach(startStopButtons => {
      startStopButtons.addListener("startPipeline", () => {
        this.__startPipeline([]);
      }, this);
      startStopButtons.addListener("startPartialPipeline", () => {
        const partialPipeline = this.getPageContext() === "workbench" ? this.__workbenchView.getSelectedNodeIDs() : this.__slideshowView.getSelectedNodeIDs();
        this.__startPipeline(partialPipeline);
      }, this);
      startStopButtons.addListener("stopPipeline", this.__stopPipeline, this);
    });

    this._add(viewsStack, {
      flex: 1
    });

    this.__updatingStudy = 0;
  },

  events: {
    "forceBackToDashboard": "qx.event.type.Event",
    "backToDashboardPressed": "qx.event.type.Event",
    "collapseNavBar": "qx.event.type.Event",
    "expandNavBar": "qx.event.type.Event",
    "slidesEdit": "qx.event.type.Event",
    "slidesAppStart": "qx.event.type.Event",
    "slidesStop": "qx.event.type.Event",
    "startSnapshot": "qx.event.type.Data",
    "startIteration": "qx.event.type.Data"
  },

  properties: {
    study: {
      check: "osparc.data.model.Study",
      nullable: true,
      apply: "_applyStudy"
    },

    pageContext: {
      check: ["workbench", "guided", "app"],
      nullable: false,
      event: "changePageContext",
      apply: "_applyPageContext"
    }
  },

  members: {
    __study: null,
    __settingStudy: null,
    __viewsStack: null,
    __workbenchView: null,
    __slideshowView: null,
    __autoSaveTimer: null,
    __lastSavedStudy: null,
    __updatingStudy: null,
    __updateThrottled: null,
    __nodesSlidesTree: null,

    setStudyData: function(studyData) {
      return new Promise((resolve, reject) => {
        if (this.__settingStudy) {
          resolve();
          return;
        }
        this.__settingStudy = true;

        this._showLoadingPage(this.tr("Starting ") + (studyData.name || this.tr("Study")));

        // Before starting a study, make sure the latest version is fetched
        const params = {
          url: {
            "studyId": studyData.uuid
          }
        };
        const promises = [
          osparc.data.Resources.getOne("studies", params),
          osparc.store.Store.getInstance().getServicesOnly()
        ];
        Promise.all(promises)
          .then(values => {
            studyData = values[0];
            const study = new osparc.data.model.Study(studyData);
            this.setStudy(study);
            resolve();
          });
      });
    },

    _applyStudy: function(study) {
      this.__settingStudy = false;

      this._showLoadingPage(this.tr("Opening ") + (study.getName() || this.tr("Study")));

      const store = osparc.store.Store.getInstance();
      store.setCurrentStudy(study);

      this.__reloadSnapshotsAndIterations();

      study.buildWorkbench();
      study.openStudy()
        .then(() => {
          this.__lastSavedStudy = study.serialize();

          this.__workbenchView.setStudy(study);
          this.__slideshowView.setStudy(study);

          study.initStudy();

          osparc.data.Resources.get("organizations")
            .then(resp => {
              const myGroupId = osparc.auth.Data.getInstance().getGroupId();
              const orgs = resp["organizations"];
              const orgIDs = [myGroupId];
              orgs.forEach(org => orgIDs.push(org["gid"]));

              if (osparc.component.permissions.Study.canGroupsWrite(study.getAccessRights(), orgIDs)) {
                this.__startAutoSaveTimer();
              } else {
                const msg = this.tr("You do not have writing permissions.<br>Changes will not be saved");
                osparc.component.message.FlashMessenger.getInstance().logAs(msg, "INFO");
              }
            });

          const pageContext = this.getPageContext();
          switch (pageContext) {
            case "guided":
            case "app":
              this.__slideshowView.startSlides(pageContext);
              break;
            default:
              this.__workbenchView.openFirstNode();
              break;
          }
          this.bind("pageContext", study.getUi(), "mode");

          const workbench = study.getWorkbench();
          workbench.addListener("retrieveInputs", e => {
            const data = e.getData();
            const node = data["node"];
            const portKey = data["portKey"];
            this.__updatePipelineAndRetrieve(node, portKey);
          }, this);

          workbench.addListener("openNode", e => {
            const nodeId = e.getData();
            this.nodeSelected(nodeId);
          }, this);

          const socket = osparc.wrapper.WebSocket.getInstance();
          socket.addListener("connect", () => {
            const params = {
              url: {
                tabId: osparc.utils.Utils.getClientSessionID()
              }
            };
            osparc.data.Resources.fetch("studies", "getActive", params)
              .then(studyData => {
                if (studyData === null) {
                  this.__noLongerActive();
                }
              })
              .catch(() => this.__noLongerActive());
          });
        })
        .catch(err => {
          let msg = "";
          if ("status" in err && err["status"] == 423) { // Locked
            msg = study.getName() + this.tr(" is already opened");
          } else {
            console.error(err);
            msg = this.tr("Error opening study");
            if ("message" in err) {
              msg += "<br>" + err["message"];
            }
          }
          osparc.component.message.FlashMessenger.getInstance().logAs(msg, "ERROR");
          this.fireEvent("forceBackToDashboard");
        })
        .finally(() => this._hideLoadingPage());

      this.__updatingStudy = 0;
    },

    __reloadSnapshotsAndIterations: function() {
      osparc.utils.DisabledPlugins.isVersionControlDisabled()
        .then(isVCDisabled => {
          if (!isVCDisabled) {
            const store = osparc.store.Store.getInstance();
            store.invalidate("snapshots");
            store.invalidate("iterations");

            const study = this.getStudy();
            study.getSnapshots()
              .then(snapshots => {
                store.setSnapshots(snapshots);
                if (snapshots.length) {
                  osparc.utils.DisabledPlugins.isMetaModelingDisabled()
                    .then(isMMDisabled => {
                      if (!isMMDisabled) {
                        study.getIterations()
                          .then(iterations => {
                            store.setIterations(iterations);
                          });
                      }
                    });
                }
              });
          }
        });
    },

    __noLongerActive: function() {
      // This might happen when the socket connection is lost and the study gets closed
      const msg = this.tr("Study was closed while you were offline");
      osparc.component.message.FlashMessenger.getInstance().logAs(msg, "ERROR");
      this.fireEvent("forceBackToDashboard");
    },

    editSlides: function() {
      if (this.getPageContext() !== "workbench") {
        return;
      }

      const study = this.getStudy();
      const nodesSlidesTree = this.__nodesSlidesTree = new osparc.component.widget.NodesSlidesTree(study);
      const title = this.tr("Edit App Mode");
      const nNodes = Object.keys(study.getWorkbench().getNodes()).length;
      const win = osparc.ui.window.Window.popUpInWindow(nodesSlidesTree, title, 370, Math.min(350, 200+(30*nNodes))).set({
        modal: false,
        clickAwayClose: false
      });
      nodesSlidesTree.addListener("changeSelectedNode", e => {
        const nodeId = e.getData();
        this.__workbenchView.getNodesTree().nodeSelected(nodeId);
        this.__workbenchView.getWorkbenchUI().nodeSelected(nodeId);
      });
      nodesSlidesTree.addListener("finished", () => {
        const slideshow = study.getUi().getSlideshow();
        slideshow.fireEvent("changeSlideshow");
        this.__nodesSlidesTree = null;
        win.close();
      });
    },


    // ------------------ START/STOP PIPELINE ------------------
    __startPipeline: function(partialPipeline = []) {
      if (!osparc.data.Permissions.getInstance().canDo("study.start", true)) {
        return;
      }

      this.getStudy().setPipelineRunning(true);
      this.updateStudyDocument(true)
        .then(() => {
          this.__requestStartPipeline(this.getStudy().getUuid(), partialPipeline);
        })
        .catch(() => {
          this.__getStudyLogger().error(null, "Run failed");
          this.getStudy().setPipelineRunning(false);
        });
    },

    __requestStartPipeline: function(studyId, partialPipeline = [], forceRestart = false) {
      const url = "/computations/" + encodeURIComponent(studyId) + ":start";
      const req = new osparc.io.request.ApiRequest(url, "POST");
      req.addListener("success", this.__onPipelinesubmitted, this);
      req.addListener("error", () => {
        this.__getStudyLogger().error(null, "Error submitting pipeline");
        this.getStudy().setPipelineRunning(false);
      }, this);
      req.addListener("fail", e => {
        if (e.getTarget().getStatus() == "403") {
          this.__getStudyLogger().error(null, "Pipeline is already running");
        } else if (e.getTarget().getStatus() == "422") {
          this.__getStudyLogger().info(null, "The pipeline is up-to-date");
          const msg = this.tr("The pipeline is up-to-date. Do you want to re-run it?");
          const win = new osparc.ui.window.Confirmation(msg).set({
            confirmText: this.tr("Run"),
            confirmAction: "create"
          });
          win.center();
          win.open();
          win.addListener("close", () => {
            if (win.getConfirmed()) {
              this.__requestStartPipeline(studyId, partialPipeline, true);
            }
          }, this);
        } else {
          this.__getStudyLogger().error(null, "Failed submitting pipeline");
        }
        this.getStudy().setPipelineRunning(false);
      }, this);

      const requestData = {
        "subgraph": partialPipeline,
        "force_restart": forceRestart
      };
      const startStopButtonsWB = this.__workbenchView.getStartStopButtons();
      if (startStopButtonsWB.getClusterId() !== null) {
        requestData["cluster_id"] = startStopButtonsWB.getClusterId();
      }
      req.setRequestData(requestData);
      req.send();
      if (partialPipeline.length) {
        this.__getStudyLogger().info(null, "Starting partial pipeline");
      } else {
        this.__getStudyLogger().info(null, "Starting pipeline");
      }

      return true;
    },

    __onPipelinesubmitted: function(e) {
      const resp = e.getTarget().getResponse();
      const pipelineId = resp.data["pipeline_id"];
      const iterationRefIds = resp.data["ref_ids"];
      this.__getStudyLogger().debug(null, "Pipeline ID " + pipelineId);
      const notGood = [null, undefined, -1];
      if (notGood.includes(pipelineId)) {
        this.__getStudyLogger().error(null, "Submission failed");
      } else {
        if (iterationRefIds) {
          this.__reloadSnapshotsAndIterations();
        }
        this.__getStudyLogger().info(null, "Pipeline started");
        /* If no projectStateUpdated comes in 60 seconds, client must
        check state of pipeline and update button accordingly. */
        const timer = setTimeout(() => {
          osparc.store.Store.getInstance().getStudyState(pipelineId);
        }, 60000);
        const socket = osparc.wrapper.WebSocket.getInstance();
        socket.getSocket().once("projectStateUpdated", jsonStr => {
          const study = JSON.parse(jsonStr);
          if (study["project_uuid"] === pipelineId) {
            clearTimeout(timer);
          }
        });
      }
    },

    __stopPipeline: function() {
      if (!osparc.data.Permissions.getInstance().canDo("study.stop", true)) {
        return;
      }

      this.__requestStopPipeline(this.getStudy().getUuid());
    },

    __requestStopPipeline: function(studyId) {
      const url = "/computations/" + encodeURIComponent(studyId) + ":stop";
      const req = new osparc.io.request.ApiRequest(url, "POST");
      req.addListener("success", () => this.__getStudyLogger().debug(null, "Pipeline aborting"), this);
      req.addListener("error", () => this.__getStudyLogger().error(null, "Error stopping pipeline"), this);
      req.addListener("fail", () => this.__getStudyLogger().error(null, "Failed stopping pipeline"), this);
      req.send();

      this.__getStudyLogger().info(null, "Stopping pipeline");
      return true;
    },
    // ------------------ START/STOP PIPELINE ------------------

    __updatePipelineAndRetrieve: function(node, portKey = null) {
      this.updateStudyDocument(false)
        .then(() => {
          if (node) {
            this.__getStudyLogger().debug(node.getNodeId(), "Retrieving inputs");
            node.retrieveInputs(portKey);
          } else {
            this.__getStudyLogger().debug(null, "Retrieving inputs");
          }
        });
      this.__getStudyLogger().debug(null, "Updating pipeline");
    },

    // overridden
    _showMainLayout: function(show) {
      this.__viewsStack.setVisibility(show ? "visible" : "excluded");
    },

    nodeSelected: function(nodeId) {
      this.__workbenchView.nodeSelected(nodeId);
      this.__slideshowView.nodeSelected(nodeId);
    },

    __getStudyLogger: function() {
      return this.__workbenchView.getLogger();
    },

    _applyPageContext: function(newCtxt) {
      switch (newCtxt) {
        case "workbench":
          this.__viewsStack.setSelection([this.__workbenchView]);
          this.__workbenchView.nodeSelected(this.getStudy().getUi().getCurrentNodeId());
          break;
        case "guided":
        case "app":
          this.__viewsStack.setSelection([this.__slideshowView]);
          this.__slideshowView.startSlides(newCtxt);
          break;
      }
    },

    __takeSnapshot: function() {
      const editSnapshotView = new osparc.component.snapshots.EditSnapshotView();
      const tagCtrl = editSnapshotView.getChildControl("tags");
      const study = this.getStudy();
      study.getSnapshots()
        .then(snapshots => {
          tagCtrl.setValue("V"+snapshots.length);
        });
      const title = this.tr("Take Snapshot");
      const win = osparc.ui.window.Window.popUpInWindow(editSnapshotView, title, 400, 180);
      editSnapshotView.addListener("takeSnapshot", () => {
        const tag = editSnapshotView.getTag();
        const message = editSnapshotView.getMessage();
        const params = {
          url: {
            "studyId": study.getUuid()
          },
          data: {
            "tag": tag,
            "message": message
          }
        };
        osparc.data.Resources.fetch("snapshots", "takeSnapshot", params)
          .then(data => {
            const store = osparc.store.Store.getInstance();
            store.getSnapshots().push(data);
          })
          .catch(err => osparc.component.message.FlashMessenger.getInstance().logAs(err.message, "ERROR"));

        win.close();
      }, this);
      editSnapshotView.addListener("cancel", () => win.close(), this);
    },

    __showSnapshots: function() {
      const study = this.getStudy();
      const snapshots = new osparc.component.snapshots.SnapshotsView(study);
      const title = this.tr("Checkpoints");
      const win = osparc.ui.window.Window.popUpInWindow(snapshots, title, 1000, 500);
      snapshots.addListener("openSnapshot", e => {
        win.close();
        const snapshotId = e.getData();
        this.fireDataEvent("startSnapshot", snapshotId);
      });
    },

    __createIterations: function() {
      console.log("createIterations not implemented yet");
    },

    __showIterations: function() {
      const study = this.getStudy();
      const iterations = new osparc.component.snapshots.IterationsView(study);
      const title = this.tr("Iterations");
      const win = osparc.ui.window.Window.popUpInWindow(iterations, title, 1000, 500);
      iterations.addListener("openIteration", e => {
        win.close();
        const studyId = e.getData();
        this.fireDataEvent("startIteration", studyId);
      });
      win.addListener("close", () => {
        iterations.unlistenToNodeUpdates();
        this.__workbenchView.listenToNodeUpdated();
      }, this);
    },

    __startAutoSaveTimer: function() {
      // Save every 3 seconds
      const interval = 3000;
      let timer = this.__autoSaveTimer = new qx.event.Timer(interval);
      timer.addListener("interval", () => {
        if (!osparc.wrapper.WebSocket.getInstance().isConnected()) {
          return;
        }
        this.__checkStudyChanges();
      }, this);
      timer.start();
    },

    didStudyChange: function() {
      const newObj = this.getStudy().serialize();
      const diffPatcher = osparc.wrapper.JsonDiffPatch.getInstance();
      const delta = diffPatcher.diff(this.__lastSavedStudy, newObj);
      if (delta) {
        let deltaKeys = Object.keys(delta);
        // lastChangeDate and creationDate should not be taken into account as data change
        [
          "creationDate",
          "lastChangeDate"
        ].forEach(prop => {
          const index = deltaKeys.indexOf(prop);
          if (index > -1) {
            deltaKeys.splice(index, 1);
          }
        });

        return deltaKeys.length;
      }
      return false;
    },

    __checkStudyChanges: function() {
      if (this.didStudyChange()) {
        if (this.__updatingStudy > 0) {
          // throttle update
          this.__updateThrottled = true;
        } else {
          this.updateStudyDocument(false);
        }
      }
    },

    __stopAutoSaveTimer: function() {
      if (this.__autoSaveTimer && this.__autoSaveTimer.isEnabled()) {
        this.__autoSaveTimer.stop();
        this.__autoSaveTimer.setEnabled(false);
      }
    },

    updateStudyDocument: function(run = false) {
      const myGrpId = osparc.auth.Data.getInstance().getGroupId();
      const orgIDs = osparc.auth.Data.getInstance().getOrgIds();
      orgIDs.push(myGrpId);
      if (!osparc.component.permissions.Study.canGroupsWrite(this.getStudy().getAccessRights(), orgIDs)) {
        return new Promise(resolve => {
          resolve();
        });
      }

      this.__updatingStudy++;
      const newObj = this.getStudy().serialize();
      return this.getStudy().updateStudy(newObj, run)
        .then(() => {
          this.__lastSavedStudy = osparc.wrapper.JsonDiffPatch.getInstance().clone(newObj);
        })
        .catch(error => {
          if ("status" in error && error.status === 409) {
            osparc.component.message.FlashMessenger.getInstance().logAs(error.message, "ERROR");
          } else {
            console.error(error);
            osparc.component.message.FlashMessenger.getInstance().logAs(this.tr("Error saving the study"), "ERROR");
          }
          this.__getStudyLogger().error(null, "Error updating pipeline");
          // Need to throw the error to be able to handle it later
          throw error;
        })
        .finally(() => {
          this.__updatingStudy--;
          if (this.__updateThrottled && this.__updatingStudy === 0) {
            this.__updateThrottled = false;
            this.updateStudyDocument(false);
          }
        });
    },

    closeEditor: function() {
      this.__stopAutoSaveTimer();
      if (this.getStudy()) {
        this.getStudy().stopStudy();
      }
      const clusterMiniView = this.__workbenchView.getStartStopButtons().getClusterMiniView();
      if (clusterMiniView) {
        clusterMiniView.setClusterId(null);
      }
      osparc.utils.Utils.closeHangingWindows();
    },

    /**
     * Destructor
     */
    destruct: function() {
      osparc.store.Store.getInstance().setCurrentStudy(null);
      this.__stopAutoSaveTimer();
    }
  }
});
