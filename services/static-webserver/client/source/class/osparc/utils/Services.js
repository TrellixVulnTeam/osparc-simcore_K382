/* ************************************************************************

   osparc - the simcore frontend

   https://osparc.io

   Copyright:
     2019 IT'IS Foundation, https://itis.swiss

   License:
     MIT: https://opensource.org/licenses/MIT

   Authors:
     * Odei Maiz (odeimaiz)

************************************************************************ */

/**
 *   Collection of methods for dealing with services data type convertions, extract
 * specific information.
 *
 * *Example*
 *
 * Here is a little example of how to use the widget.
 *
 * <pre class='javascript'>
 *   let latestSrv = osparc.utils.Services.getLatest(services, key);
 * </pre>
 */

qx.Class.define("osparc.utils.Services", {
  type: "static",

  statics: {
    TYPES: {
      parameter: {
        label: qx.locale.Manager.tr("Parameter"),
        icon: "@FontAwesome5Solid/sliders-h/",
        sorting: 0
      },
      file: {
        label: qx.locale.Manager.tr("File"),
        icon: "@FontAwesome5Solid/file/",
        sorting: 1
      },
      iterator: {
        label: qx.locale.Manager.tr("Iterator"),
        icon: "@FontAwesome5Solid/copy/",
        sorting: 2
      },
      computational: {
        label: qx.locale.Manager.tr("Computational"),
        icon: "@FontAwesome5Solid/cogs/",
        sorting: 3
      },
      dynamic: {
        label: qx.locale.Manager.tr("Interactive"),
        icon: "@FontAwesome5Solid/mouse-pointer/",
        sorting: 4
      },
      probe: {
        label: qx.locale.Manager.tr("Probe"),
        icon: "@FontAwesome5Solid/thermometer/",
        sorting: 5
      }
    },

    servicesCached: {},

    addServiceToCache: function(service) {
      this.servicesCached = Object.assign(this.servicesCached, service);
    },

    servicesToCache: function(services) {
      this.servicesCached = {};
      this.__addExtraInfo(services);
      this.servicesCached = Object.assign(this.servicesCached, services);
    },

    getTypes: function() {
      return Object.keys(this.TYPES);
    },

    getType: function(type) {
      return this.TYPES[type.trim().toLowerCase()];
    },

    getIcon: function(type) {
      const typeInfo = this.getType(type);
      if (typeInfo) {
        return typeInfo["icon"];
      }
      return null;
    },

    getSorting: function(type) {
      const typeInfo = this.getType(type);
      if (typeInfo) {
        return typeInfo["sorting"];
      }
      return 0;
    },

    sortObjectsBasedOn: function(servicesArray, basedOn) {
      if (basedOn === undefined) {
        basedOn = {
          "sort": "hits",
          "order": "down"
        };
      }
      servicesArray.sort((a, b) => {
        if (basedOn.sort === "hits") {
          if (a[basedOn.sort] !== b[basedOn.sort]) {
            if (basedOn.order === "down") {
              return b[basedOn.sort] - a[basedOn.sort];
            }
            return a[basedOn.sort] - b[basedOn.sort];
          }
          return a["name"].localeCompare(b["name"]);
        } else if (basedOn.sort === "name") {
          if (basedOn.order === "down") {
            return a["name"].localeCompare(b["name"]);
          }
          return b["name"].localeCompare(a["name"]);
        }
        return 0;
      });
    },

    addHits: function(servicesArray) {
      const favServices = osparc.utils.Utils.localCache.getFavServices();
      servicesArray.forEach(service => {
        const found = Object.keys(favServices).find(favSrv => favSrv === service["key"]);
        service.hits = found ? favServices[found]["hits"] : 0;
      });
    },

    convertArrayToObject: function(servicesArray) {
      let services = {};
      for (let i = 0; i < servicesArray.length; i++) {
        const service = servicesArray[i];
        if (!Object.prototype.hasOwnProperty.call(services, service.key)) {
          services[service.key] = {};
        }
        if (!Object.prototype.hasOwnProperty.call(services[service.key], service.version)) {
          services[service.key][service.version] = {};
        }
        services[service.key][service.version] = service;
      }
      return services;
    },

    convertObjectToArray: function(servicesObject) {
      let services = [];
      for (const key in servicesObject) {
        const serviceVersions = servicesObject[key];
        for (const serviceVersion in serviceVersions) {
          services.push(serviceVersions[serviceVersion]);
        }
      }
      return services;
    },

    getFromObject: function(services, key, version) {
      if (key in services) {
        const serviceVersions = services[key];
        if (version in serviceVersions) {
          return serviceVersions[version];
        }
      }
      return null;
    },

    getFromArray: function(services, key, version) {
      for (let i=0; i<services.length; i++) {
        if (services[i].key === key && services[i].version === version) {
          return services[i];
        }
      }
      return null;
    },

    getVersions: function(services, key) {
      let versions = [];
      if (key in services) {
        const serviceVersions = services[key];
        versions = versions.concat(Object.keys(serviceVersions));
        versions.sort(osparc.utils.Utils.compareVersionNumbers);
      }
      return versions;
    },

    getLatest: function(services, key) {
      if (key in services) {
        const versions = this.getVersions(services, key);
        return services[key][versions[versions.length - 1]];
      }
      return null;
    },

    isOwner: function(serviceData) {
      const orgIDs = osparc.auth.Data.getInstance().getOrgIds();
      orgIDs.push(osparc.auth.Data.getInstance().getGroupId());
      return osparc.component.permissions.Service.canAnyGroupWrite(serviceData["access_rights"], orgIDs);
    },

    getOwnedServices: function(services, key) {
      const orgIDs = osparc.auth.Data.getInstance().getOrgIds();
      orgIDs.push(osparc.auth.Data.getInstance().getGroupId());
      const ownedVersions = [];
      if (key in services) {
        this.getVersions(services, key).forEach(version => {
          if (osparc.component.permissions.Service.canAnyGroupWrite(services[key][version]["access_rights"], orgIDs)) {
            ownedVersions.push(version);
          }
        });
        ownedVersions.sort(osparc.utils.Utils.compareVersionNumbers);
      }
      return ownedVersions;
    },

    /**
     * Compatibility check:
     * - compIOFields of src inputs need to be in dest inputs
     * - compIOFields of src outputs need to be in dest outputs
     */
    __areNodesCompatible: function(srcNode, destNode) {
      const compIOFields = ["keyId"];
      let compatible = true;

      // inputs
      Object.keys(srcNode["inputs"]).forEach(inputKey => {
        if (!(inputKey in destNode["inputs"])) {
          compatible = false;
        }
        Object.keys(srcNode["inputs"][inputKey]).forEach(inputField => {
          if (compIOFields.includes(inputField)) {
            if (!(inputField in destNode["inputs"][inputKey]) || srcNode["inputs"][inputKey][inputField] !== destNode["inputs"][inputKey][inputField]) {
              compatible = false;
            }
          }
        });
      });

      // outputs
      Object.keys(srcNode["outputs"]).forEach(outputKey => {
        if (!(outputKey in destNode["outputs"])) {
          compatible = false;
        }
        Object.keys(srcNode["outputs"][outputKey]).forEach(outputField => {
          if (compIOFields.includes(outputField)) {
            if (!(outputField in destNode["outputs"][outputKey]) || srcNode["outputs"][outputKey][outputField] !== destNode["outputs"][outputKey][outputField]) {
              compatible = false;
            }
          }
        });
      });

      return compatible;
    },

    getLatestCompatible: function(services, srcKey, srcVersion) {
      const srcNode = this.getFromObject(services, srcKey, srcVersion);
      let versions = this.getVersions(services, srcKey);
      // only allow patch versions
      versions = versions.filter(version => {
        const v1 = version.split(".");
        const v2 = srcVersion.split(".");
        return (v1[0] === v2[0] && v1[1] === v2[1]);
      });
      versions.reverse();
      const idx = versions.indexOf(srcVersion);
      if (idx > -1) {
        versions.length = idx+1;
        for (let i=0; i<versions.length; i++) {
          const destVersion = versions[i];
          const destNode = this.getFromObject(services, srcKey, destVersion);
          if (this.__areNodesCompatible(srcNode, destNode)) {
            return destNode;
          }
        }
      }
      return srcNode;
    },

    getMetaData: function(key, version) {
      let metaData = null;
      if (key && version) {
        const services = osparc.utils.Services.servicesCached;
        metaData = this.getFromObject(services, key, version);
        if (metaData) {
          metaData = osparc.utils.Utils.deepCloneObject(metaData);
          return metaData;
        }
      }
      return null;
    },

    isDeprecated: function(metadata) {
      if (metadata && "deprecated" in metadata && ![null, undefined].includes(metadata["deprecated"])) {
        const depTime = new Date(metadata["deprecated"]);
        const now = new Date();
        return depTime.getTime() < now.getTime();
      }
      return false;
    },

    DEPRECATED_SERVICE: qx.locale.Manager.tr("Service deprecated"),
    DEPRECATED_DYNAMIC_INSTRUCTIONS: qx.locale.Manager.tr("Please, download the Service data and upload them to an updated version"),
    DEPRECATED_COMPUTATIONAL_INSTRUCTIONS: qx.locale.Manager.tr("Please, instantiate an updated version"),

    getFilePicker: function() {
      return this.self().getLatest(this.servicesCached, "simcore/services/frontend/file-picker");
    },

    getParametersMetadata: function() {
      const parametersMetadata = [];
      for (const key in this.servicesCached) {
        if (key.includes("simcore/services/frontend/parameter/")) {
          const latest = this.self().getLatest(this.servicesCached, key);
          if (latest) {
            parametersMetadata.push(latest);
          }
        }
      }
      return parametersMetadata;
    },

    getParameterMetadata: function(type) {
      return this.self().getLatest(this.servicesCached, "simcore/services/frontend/parameter/"+type);
    },

    getProbeMetadata: function(type) {
      return this.self().getLatest(this.servicesCached, "simcore/services/frontend/iterator-consumer/probe/"+type);
    },

    getNodesGroup: function() {
      return this.self().getLatest(this.servicesCached, "simcore/services/frontend/nodes-group");
    },

    __addExtraInfo: function(services) {
      Object.values(services).forEach(serviceWVersion => {
        Object.values(serviceWVersion).forEach(service => {
          if (osparc.data.model.Node.isComputational(service)) {
            osparc.component.metadata.Quality.attachQualityToObject(service);
          }
        });
      });
    },

    removeFileToKeyMap: function(service) {
      [
        "inputs",
        "outputs"
      ].forEach(inOut => {
        if (inOut in service) {
          for (const key in service[inOut]) {
            if ("fileToKeyMap" in service[inOut][key]) {
              delete service[inOut][key]["fileToKeyMap"];
            }
          }
        }
      });
    },

    getUniqueServicesFromWorkbench: function(workbench) {
      const services = [];
      Object.values(workbench).forEach(node => {
        const service = {
          key: node["key"],
          version: node["version"]
        };
        const idx = services.findIndex(existingSrv => existingSrv.key === service.key && existingSrv.version === service.version);
        if (idx === -1) {
          services.push(service);
        }
      });
      return services;
    }
  }
});
